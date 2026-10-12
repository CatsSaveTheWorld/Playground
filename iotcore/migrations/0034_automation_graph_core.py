import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


GRAPH_VERSION = 1


def migrate_legacy_automations(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")
    AutomationNode = apps.get_model("iotcore", "AutomationNode")
    ConditionItem = apps.get_model("iotcore", "AutomationConditionItem")
    ActionItem = apps.get_model("iotcore", "AutomationActionItem")
    Edge = apps.get_model("iotcore", "AutomationEdge")
    Step = apps.get_model("iotcore", "Step")
    Trigger = apps.get_model("iotcore", "Trigger")
    Action = apps.get_model("iotcore", "Action")

    for automation in Automation.objects.all().iterator():
        steps = list(
            Step.objects.filter(automation_id=automation.pk).order_by("order", "id")
        )
        # Empty/malformed legacy automations deliberately stay on version 0 so
        # deployment never turns a previously inspectable row into a migration
        # failure. The legacy executor remains the rollback/fallback path.
        if not steps:
            continue
        if any(not Action.objects.filter(step_id=step.pk).exists() for step in steps):
            continue

        start = AutomationNode.objects.create(
            automation_id=automation.pk,
            key=uuid.uuid4(),
            node_type="start",
            title="START",
            position_x=40,
            position_y=80,
        )
        end = AutomationNode.objects.create(
            automation_id=automation.pk,
            key=uuid.uuid4(),
            node_type="end",
            title="END",
            position_x=960,
            position_y=80 + max(len(steps) - 1, 0) * 180 // 2,
        )

        start_order = 0
        for branch_index, step in enumerate(steps):
            y = 80 + branch_index * 180
            triggers = list(
                Trigger.objects.filter(step_id=step.pk).order_by("order", "id")
            )
            actions = list(
                Action.objects.filter(step_id=step.pk).order_by("order", "id")
            )
            action_node = AutomationNode.objects.create(
                automation_id=automation.pk,
                key=uuid.uuid4(),
                node_type="action",
                title=f"Step {step.order} 동작",
                position_x=650,
                position_y=y,
                enabled=step.enabled,
                legacy_step_id=step.pk,
            )
            for action in actions:
                ActionItem.objects.create(
                    node_id=action_node.pk,
                    order=action.order,
                    action_type=action.action_type,
                    device_id=action.device_id,
                    function=action.function,
                    parameter=action.parameter,
                    target_automation_id=action.target_automation_id,
                    delay=action.delay,
                    delay_position=action.delay_position,
                    legacy_action_id=action.pk,
                )
            Edge.objects.create(
                automation_id=automation.pk,
                source_id=action_node.pk,
                target_id=end.pk,
                outcome="always",
                order=1,
            )

            if not triggers:
                start_order += 1
                Edge.objects.create(
                    automation_id=automation.pk,
                    source_id=start.pk,
                    target_id=action_node.pk,
                    outcome="always",
                    order=start_order,
                )
                continue

            if step.trigger_operator == "or" and len(triggers) > 1:
                for offset, trigger in enumerate(triggers):
                    condition_node = AutomationNode.objects.create(
                        automation_id=automation.pk,
                        key=uuid.uuid4(),
                        node_type="condition",
                        title=f"Step {step.order} 조건 {offset + 1}",
                        position_x=330,
                        position_y=y + offset * 70,
                        enabled=step.enabled,
                        legacy_step_id=step.pk,
                        last_result=step.last_result,
                        next_run_at=step.next_run_at,
                        last_triggered_at=step.last_triggered_at,
                    )
                    ConditionItem.objects.create(
                        node_id=condition_node.pk,
                        order=1,
                        condition_type=trigger.trigger_type,
                        config=trigger.config or {},
                        legacy_trigger_id=trigger.pk,
                    )
                    start_order += 1
                    Edge.objects.create(
                        automation_id=automation.pk,
                        source_id=start.pk,
                        target_id=condition_node.pk,
                        outcome="always",
                        order=start_order,
                    )
                    Edge.objects.create(
                        automation_id=automation.pk,
                        source_id=condition_node.pk,
                        target_id=action_node.pk,
                        outcome="true",
                        order=1,
                    )
                    Edge.objects.create(
                        automation_id=automation.pk,
                        source_id=condition_node.pk,
                        target_id=end.pk,
                        outcome="false",
                        order=1,
                    )
            else:
                condition_node = AutomationNode.objects.create(
                    automation_id=automation.pk,
                    key=uuid.uuid4(),
                    node_type="condition",
                    title=f"Step {step.order} 조건",
                    position_x=330,
                    position_y=y,
                    enabled=step.enabled,
                    legacy_step_id=step.pk,
                    last_result=step.last_result,
                    next_run_at=step.next_run_at,
                    last_triggered_at=step.last_triggered_at,
                )
                for trigger in triggers:
                    ConditionItem.objects.create(
                        node_id=condition_node.pk,
                        order=trigger.order,
                        condition_type=trigger.trigger_type,
                        config=trigger.config or {},
                        legacy_trigger_id=trigger.pk,
                    )
                start_order += 1
                Edge.objects.create(
                    automation_id=automation.pk,
                    source_id=start.pk,
                    target_id=condition_node.pk,
                    outcome="always",
                    order=start_order,
                )
                Edge.objects.create(
                    automation_id=automation.pk,
                    source_id=condition_node.pk,
                    target_id=action_node.pk,
                    outcome="true",
                    order=1,
                )
                Edge.objects.create(
                    automation_id=automation.pk,
                    source_id=condition_node.pk,
                    target_id=end.pk,
                    outcome="false",
                    order=1,
                )

        automation.graph_version = GRAPH_VERSION
        automation.graph_revision = 1
        automation.save(update_fields=["graph_version", "graph_revision"])


def reverse_graph_data(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")
    Automation.objects.update(graph_version=0, graph_revision=0)
    # Cascading from AutomationNode removes graph-only rows while all legacy
    # Step/Trigger/Action rows remain untouched.
    apps.get_model("iotcore", "AutomationNode").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0033_automation_voice_aliases"),
    ]

    operations = [
        migrations.AddField(
            model_name="automation",
            name="graph_revision",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="automation",
            name="graph_version",
            field=models.PositiveSmallIntegerField(db_index=True, default=0),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="entry_node_key",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="graph_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="graph_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="graph_item_order",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="graph_node_key",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="AutomationNode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("node_type", models.CharField(choices=[("start", "시작"), ("condition", "조건"), ("action", "동작"), ("delay", "지연"), ("end", "종료")], max_length=20)),
                ("title", models.CharField(blank=True, max_length=100)),
                ("position_x", models.IntegerField(default=0)),
                ("position_y", models.IntegerField(default=0)),
                ("enabled", models.BooleanField(default=True)),
                ("delay_seconds", models.PositiveIntegerField(default=0)),
                ("last_result", models.BooleanField(default=False)),
                ("next_run_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_triggered_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("automation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="graph_nodes", to="iotcore.automation")),
                ("legacy_step", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="graph_nodes", to="iotcore.step")),
            ],
            options={"ordering": ["position_y", "position_x", "id"]},
        ),
        migrations.CreateModel(
            name="AutomationConditionItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=1)),
                ("condition_type", models.CharField(choices=[("schedule", "날짜 / 시간"), ("device_state", "기기 상태 / 변화"), ("mqtt_event", "MQTT 이벤트"), ("weather", "현재 날씨")], max_length=20)),
                ("config", models.JSONField(default=dict)),
                ("legacy_trigger", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="graph_condition_items", to="iotcore.trigger")),
                ("node", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conditions", to="iotcore.automationnode")),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.CreateModel(
            name="AutomationActionItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=1)),
                ("action_type", models.CharField(choices=[("device", "개별 기기 동작"), ("automation", "다른 자동화 실행")], default="device", max_length=20)),
                ("function", models.CharField(blank=True, max_length=100)),
                ("parameter", models.JSONField(blank=True, null=True)),
                ("delay", models.PositiveIntegerField(default=0)),
                ("delay_position", models.CharField(choices=[("before", "동작 전"), ("after", "동작 후")], default="after", max_length=10)),
                ("device", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="graph_action_items", to="iotcore.device")),
                ("legacy_action", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="graph_action_items", to="iotcore.action")),
                ("node", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="actions", to="iotcore.automationnode")),
                ("target_automation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="graph_referenced_by_actions", to="iotcore.automation")),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.CreateModel(
            name="AutomationEdge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outcome", models.CharField(choices=[("always", "항상"), ("true", "참"), ("false", "거짓")], default="always", max_length=10)),
                ("order", models.PositiveIntegerField(default=1)),
                ("automation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="graph_edges", to="iotcore.automation")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outgoing_edges", to="iotcore.automationnode")),
                ("target", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="incoming_edges", to="iotcore.automationnode")),
            ],
            options={"ordering": ["source_id", "outcome", "order", "id"]},
        ),
        migrations.CreateModel(
            name="AutomationNodeRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("node_key", models.UUIDField()),
                ("node_type", models.CharField(choices=[("start", "시작"), ("condition", "조건"), ("action", "동작"), ("delay", "지연"), ("end", "종료")], max_length=20)),
                ("queue_order", models.PositiveBigIntegerField(default=0)),
                ("status", models.CharField(choices=[("pending", "대기"), ("waiting", "지연 대기"), ("running", "실행 중"), ("success", "성공"), ("failed", "실패"), ("cancelled", "취소"), ("skipped", "건너뜀")], db_index=True, default="pending", max_length=20)),
                ("available_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("automation_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="node_runs", to="iotcore.automationrun")),
                ("node", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="runs", to="iotcore.automationnode")),
            ],
            options={"ordering": ["queue_order", "id"]},
        ),
        migrations.AddConstraint(
            model_name="automationnode",
            constraint=models.UniqueConstraint(fields=("automation", "key"), name="unique_automation_node_key"),
        ),
        migrations.AddConstraint(
            model_name="automationconditionitem",
            constraint=models.UniqueConstraint(fields=("node", "order"), name="unique_graph_condition_order"),
        ),
        migrations.AddConstraint(
            model_name="automationactionitem",
            constraint=models.UniqueConstraint(fields=("node", "order"), name="unique_graph_action_order"),
        ),
        migrations.AddConstraint(
            model_name="automationedge",
            constraint=models.UniqueConstraint(fields=("source", "outcome", "order"), name="unique_graph_edge_order"),
        ),
        migrations.AddConstraint(
            model_name="automationnoderun",
            constraint=models.UniqueConstraint(fields=("automation_run", "node_key"), name="unique_run_node_key"),
        ),
        migrations.AddIndex(
            model_name="automationnoderun",
            index=models.Index(fields=["status", "available_at", "queue_order"], name="iotcore_node_run_queue_idx"),
        ),
        migrations.AddConstraint(
            model_name="automationrun",
            constraint=models.UniqueConstraint(fields=("automation", "entry_node_key", "scheduled_for"), name="unique_graph_entry_run_time"),
        ),
        migrations.AddConstraint(
            model_name="automationrun",
            constraint=models.UniqueConstraint(fields=("automation", "entry_node_key", "source_event_id"), name="unique_graph_entry_event"),
        ),
        migrations.RunPython(migrate_legacy_automations, reverse_graph_data),
    ]

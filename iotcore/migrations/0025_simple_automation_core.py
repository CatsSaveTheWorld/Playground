from django.db import migrations, models
import django.db.models.deletion


def forward_data(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")
    AutomationGroup = apps.get_model("iotcore", "AutomationGroup")
    Step = apps.get_model("iotcore", "Step")
    Trigger = apps.get_model("iotcore", "Trigger")
    Action = apps.get_model("iotcore", "Action")
    Sequence = apps.get_model("iotcore", "Sequence")
    SequenceStep = apps.get_model("iotcore", "SequenceStep")
    SequenceStepAction = apps.get_model("iotcore", "SequenceStepAction")
    SequenceStepCondition = apps.get_model("iotcore", "SequenceStepCondition")
    SequenceRun = apps.get_model("iotcore", "SequenceRun")
    SequenceStepRun = apps.get_model("iotcore", "SequenceStepRun")
    AutomationRun = apps.get_model("iotcore", "AutomationRun")
    ActionRun = apps.get_model("iotcore", "ActionRun")

    # Existing Automation rows are scheduled by definition.
    Automation.objects.filter(automation_type="").update(automation_type="scheduled")

    # Normalize existing AutomationTrigger rows (now Step) and materialize old
    # one-row trigger forms as child Trigger rows.
    for automation in Automation.objects.all().iterator():
        steps = list(Step.objects.filter(automation_id=automation.pk).order_by("id"))
        for order, step in enumerate(steps, start=1):
            step.order = order
            step.save(update_fields=["order"])
            legacy_type = getattr(step, "trigger_type", "set")
            config = getattr(step, "config", {}) or {}
            if legacy_type and legacy_type != "set" and not Trigger.objects.filter(step_id=step.pk).exists():
                mapped = {
                    "time": "schedule",
                    "mqtt_event": "mqtt_event",
                    "device_state": "device_state",
                }.get(legacy_type)
                if mapped:
                    Trigger.objects.create(
                        automation_id=automation.pk,
                        step_id=step.pk,
                        trigger_type=mapped,
                        config=config,
                        order=1,
                    )

    # Any old shared action without a Step is attached to a single new Step.
    for automation in Automation.objects.all().iterator():
        orphan_actions = list(Action.objects.filter(automation_id=automation.pk, step_id__isnull=True).order_by("order", "id"))
        if orphan_actions:
            step = Step.objects.filter(automation_id=automation.pk).order_by("order", "id").first()
            if step is None:
                step = Step.objects.create(automation_id=automation.pk, order=1)
            for order, action in enumerate(orphan_actions, start=1):
                action.step_id = step.pk
                action.order = order
                action.save(update_fields=["step", "order"])

    # Convert legacy Condition ownership to Step ownership if any rows survived
    # the prior transition migrations.
    for trigger in Trigger.objects.filter(step_id__isnull=True).order_by("automation_id", "order", "id"):
        step = None
        if getattr(trigger, "action_id", None):
            action = Action.objects.filter(pk=trigger.action_id).first()
            if action is not None:
                step = Step.objects.filter(pk=action.step_id).first()
        if step is None and getattr(trigger, "automation_id", None):
            step = Step.objects.filter(automation_id=trigger.automation_id).order_by("order", "id").first()
        if step is not None:
            trigger.step_id = step.pk
            trigger.save(update_fields=["step"])

    # Migrate every Sequence into an immediate Automation using the exact same
    # Step / Trigger / Action graph.
    sequence_map = {}
    sequence_step_map = {}
    for sequence in Sequence.objects.select_related("group").all().iterator():
        group_id = None
        if sequence.group_id:
            group, _ = AutomationGroup.objects.get_or_create(
                name=sequence.group.name,
                defaults={"order": sequence.group.order},
            )
            group_id = group.pk
        automation = Automation.objects.create(
            name=sequence.name,
            description=sequence.description or "",
            automation_type="immediate",
            group_id=group_id,
            is_favorite=sequence.is_favorite,
            enabled=True,
        )
        sequence_map[sequence.pk] = automation.pk

        for step in SequenceStep.objects.filter(sequence_id=sequence.pk).order_by("order", "id"):
            new_step = Step.objects.create(
                automation_id=automation.pk,
                order=step.order,
                trigger_operator=step.condition_operator or "and",
                enabled=True,
            )
            sequence_step_map[step.pk] = new_step.pk

            conditions = list(SequenceStepCondition.objects.filter(step_id=step.pk).order_by("order", "id"))
            for condition in conditions:
                trigger_type = condition.condition_type
                config = dict(condition.config or {})
                if trigger_type == "time_window":
                    trigger_type = "schedule"
                    config.setdefault("schedule_type", "weekly")
                    config.setdefault("time_mode", "window")
                    config.setdefault("weekdays", list(range(7)))
                elif trigger_type == "event_value":
                    trigger_type = "mqtt_event"
                Trigger.objects.create(
                    automation_id=automation.pk,
                    step_id=new_step.pk,
                    trigger_type=trigger_type,
                    config=config,
                    order=condition.order,
                )

            actions = list(SequenceStepAction.objects.filter(step_id=step.pk).order_by("order", "id"))
            if not actions and step.device_id and step.function:
                actions = [step]
            for order, old_action in enumerate(actions, start=1):
                Action.objects.create(
                    automation_id=automation.pk,
                    step_id=new_step.pk,
                    order=getattr(old_action, "order", order) or order,
                    action_type="device",
                    device_id=old_action.device_id,
                    function=old_action.function,
                    parameter=old_action.parameter,
                    delay=old_action.delay,
                    delay_position=getattr(old_action, "delay_position", "after") or "after",
                )

    # Replace "run sequence" actions with "run automation" references.
    for action in Action.objects.exclude(sequence_id__isnull=True).order_by("id").iterator():
        target_id = sequence_map.get(action.sequence_id)
        if target_id:
            action.action_type = "automation"
            action.target_automation_id = target_id
            action.device_id = None
            action.function = ""
            action.parameter = None
            action.save(update_fields=["action_type", "target_automation", "device", "function", "parameter"])

    # Populate the unified run metadata for existing scheduled runs.
    for run in AutomationRun.objects.select_related("automation", "step").all().iterator():
        run.automation_name = run.automation.name if run.automation_id else ""
        payload_type = (run.trigger_payload or {}).get("type")
        if run.scheduled_for:
            run.source = "scheduler"
        elif payload_type == "weather":
            run.source = "weather"
        elif payload_type == "device_state":
            run.source = "device"
        else:
            run.source = "mqtt"
        run.save(update_fields=["automation_name", "source"])

    # Preserve Sequence history by copying it into AutomationRun/ActionRun.
    for old_run in SequenceRun.objects.all().iterator():
        automation_id = sequence_map.get(old_run.sequence_id)
        source = "automation" if old_run.trigger == "automation" else "manual"
        new_run = AutomationRun.objects.create(
            automation_id=automation_id,
            automation_name=old_run.sequence_name or "",
            source=source,
            status=old_run.status,
            scheduled_for=old_run.scheduled_for,
            source_event_id=None,  # avoid cross-generation uniqueness collisions
            trigger_payload=old_run.trigger_payload or {},
            started_at=old_run.started_at,
            finished_at=old_run.finished_at,
            message=old_run.message or "",
        )
        for old_step_run in SequenceStepRun.objects.filter(sequence_run_id=old_run.pk).order_by("step_order", "id"):
            ActionRun.objects.create(
                automation_run_id=new_run.pk,
                action_id=None,
                step_order=old_step_run.step_order,
                order=1,
                status=old_step_run.status,
                message=old_step_run.message or "",
                started_at=old_step_run.started_at,
                finished_at=old_step_run.finished_at,
            )

    # Fill step_order on existing action histories.
    for action_run in ActionRun.objects.select_related("action__step").all().iterator():
        if action_run.action_id and action_run.action.step_id:
            action_run.step_order = action_run.action.step.order
            action_run.save(update_fields=["step_order"])


def reverse_data(apps, schema_editor):
    # This migration intentionally collapses two runtime models into one.  A
    # reverse migration can recreate schema but cannot faithfully split new
    # Automation rows back into Sequence vs Automation data.
    pass


class Migration(migrations.Migration):
    dependencies = [("iotcore", "0024_unified_step_editor")]

    operations = [
        migrations.AddField(
            model_name="automation",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="automation",
            name="automation_type",
            field=models.CharField(
                choices=[("immediate", "즉시 실행"), ("scheduled", "예약 실행")],
                db_index=True,
                default="scheduled",
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name="automationgroup",
            options={"ordering": ["order", "name", "id"], "verbose_name": "자동화 그룹", "verbose_name_plural": "자동화 그룹"},
        ),
        migrations.AlterModelOptions(
            name="automation",
            options={"ordering": ["name", "id"], "verbose_name": "자동화", "verbose_name_plural": "자동화"},
        ),
        migrations.RenameModel(old_name="AutomationTrigger", new_name="Step"),
        migrations.RenameField(model_name="step", old_name="condition_operator", new_name="trigger_operator"),
        migrations.AddField(model_name="step", name="order", field=models.PositiveIntegerField(default=0)),
        migrations.RenameModel(old_name="AutomationCondition", new_name="Trigger"),
        migrations.RemoveConstraint(model_name="trigger", name="unique_automation_trigger_condition_order"),
        migrations.RenameField(model_name="trigger", old_name="condition_type", new_name="trigger_type"),
        migrations.RenameField(model_name="trigger", old_name="trigger", new_name="step"),
        migrations.RenameModel(old_name="AutomationAction", new_name="Action"),
        migrations.RemoveConstraint(model_name="action", name="unique_automation_trigger_action_order"),
        migrations.RenameField(model_name="action", old_name="trigger", new_name="step"),
        migrations.AddField(
            model_name="action",
            name="target_automation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="referenced_by_actions", to="iotcore.automation"),
        ),
        migrations.AddField(
            model_name="action",
            name="delay_position",
            field=models.CharField(choices=[("before", "동작 전"), ("after", "동작 후")], default="after", max_length=10),
        ),
        migrations.RemoveConstraint(model_name="automationrun", name="unique_automation_trigger_run_time"),
        migrations.RemoveConstraint(model_name="automationrun", name="unique_automation_trigger_source_event"),
        migrations.RenameField(model_name="automationrun", old_name="trigger", new_name="step"),
        migrations.AddField(model_name="automationrun", name="automation_name", field=models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField(model_name="automationrun", name="source", field=models.CharField(default="manual", max_length=20)),
        migrations.RenameModel(old_name="AutomationActionRun", new_name="ActionRun"),
        migrations.RenameField(model_name="actionrun", old_name="automation_action", new_name="action"),
        migrations.AddField(model_name="actionrun", name="step_order", field=models.PositiveIntegerField(default=1)),
        migrations.AlterModelOptions(name="step", options={"ordering": ["order", "id"]}),
        migrations.AlterModelOptions(name="trigger", options={"ordering": ["order", "id"]}),
        migrations.AlterModelOptions(name="action", options={"ordering": ["order", "id"]}),
        migrations.AlterModelOptions(name="actionrun", options={"ordering": ["step_order", "order", "id"]}),
        migrations.RunPython(forward_data, reverse_data),
        # Remove short-lived/legacy ownership columns after data is consolidated.
        migrations.RemoveField(model_name="step", name="trigger_type"),
        migrations.RemoveField(model_name="step", name="config"),
        migrations.RemoveField(model_name="trigger", name="automation"),
        migrations.RemoveField(model_name="trigger", name="action"),
        migrations.RemoveField(model_name="action", name="automation"),
        migrations.RemoveField(model_name="action", name="sequence"),
        migrations.RemoveField(model_name="actionrun", name="sequence_run"),
        migrations.AlterField(model_name="step", name="order", field=models.PositiveIntegerField()),
        migrations.AlterField(
            model_name="step",
            name="trigger_operator",
            field=models.CharField(choices=[("and", "모든 트리거 만족 (AND)"), ("or", "하나 이상 만족 (OR)")], default="and", max_length=3),
        ),
        migrations.AlterField(
            model_name="trigger",
            name="trigger_type",
            field=models.CharField(choices=[("schedule", "날짜 / 시간"), ("device_state", "기기 상태 / 변화"), ("mqtt_event", "MQTT 이벤트"), ("weather", "현재 날씨")], max_length=20),
        ),
        migrations.AlterField(
            model_name="action",
            name="action_type",
            field=models.CharField(choices=[("device", "개별 기기 동작"), ("automation", "다른 자동화 실행")], default="device", max_length=20),
        ),
        migrations.AlterField(model_name="action", name="step", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="actions", to="iotcore.step")),
        migrations.AlterField(model_name="trigger", name="step", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="triggers", to="iotcore.step")),
        migrations.AddConstraint(model_name="step", constraint=models.UniqueConstraint(fields=("automation", "order"), name="unique_automation_step_order")),
        migrations.AddConstraint(model_name="trigger", constraint=models.UniqueConstraint(fields=("step", "order"), name="unique_step_trigger_order")),
        migrations.AddConstraint(model_name="action", constraint=models.UniqueConstraint(fields=("step", "order"), name="unique_step_action_order")),
        migrations.AlterField(model_name="automationrun", name="source", field=models.CharField(choices=[("manual", "수동"), ("scheduler", "스케줄러"), ("mqtt", "MQTT"), ("device", "기기 상태"), ("weather", "날씨"), ("automation", "다른 자동화"), ("api", "API"), ("ai", "AI")], db_index=True, default="manual", max_length=20)),
        migrations.AddConstraint(model_name="automationrun", constraint=models.UniqueConstraint(fields=("step", "scheduled_for"), name="unique_step_run_time")),
        migrations.AddConstraint(model_name="automationrun", constraint=models.UniqueConstraint(fields=("step", "source_event_id"), name="unique_step_source_event")),
        migrations.DeleteModel(name="SequenceStepRun"),
        migrations.DeleteModel(name="SequenceRun"),
        migrations.DeleteModel(name="SequenceStepCondition"),
        migrations.DeleteModel(name="SequenceStepAction"),
        migrations.DeleteModel(name="SequenceStep"),
        migrations.DeleteModel(name="Sequence"),
        migrations.DeleteModel(name="SequenceGroup"),
    ]

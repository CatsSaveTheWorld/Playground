from django.db import migrations, models
import django.db.models.deletion


def migrate_step_data(apps, schema_editor):
    SequenceStep = apps.get_model("iotcore", "SequenceStep")
    SequenceStepAction = apps.get_model("iotcore", "SequenceStepAction")
    AutomationAction = apps.get_model("iotcore", "AutomationAction")
    AutomationTrigger = apps.get_model("iotcore", "AutomationTrigger")

    # Legacy SequenceStep stored exactly one action on the Step itself.
    # Preserve every existing sequence by materializing that action as the
    # first child action.  New code reads children first and keeps legacy
    # columns only as rollback/compatibility data.
    for step in SequenceStep.objects.all().iterator():
        if step.device_id and step.function:
            SequenceStepAction.objects.get_or_create(
                step_id=step.pk,
                order=1,
                defaults={
                    "device_id": step.device_id,
                    "function": step.function,
                    "parameter": step.parameter,
                    "delay": step.delay,
                    "delay_position": step.delay_position,
                },
            )

    # The short-lived unified-editor prototype stored one shared DO list as
    # AutomationAction(trigger=NULL).  The final model is Step-centric again:
    # each AutomationTrigger (= Step) owns N conditions and N actions.  Copy
    # shared actions into any Step that does not already have its own actions.
    automation_ids = list(
        AutomationAction.objects.filter(trigger_id__isnull=True)
        .values_list("automation_id", flat=True)
        .distinct()
    )
    for automation_id in automation_ids:
        shared = list(
            AutomationAction.objects.filter(
                automation_id=automation_id,
                trigger_id__isnull=True,
            ).order_by("order", "id")
        )
        triggers = AutomationTrigger.objects.filter(
            automation_id=automation_id
        ).order_by("id")
        for trigger in triggers:
            if AutomationAction.objects.filter(trigger_id=trigger.pk).exists():
                continue
            for order, action in enumerate(shared, start=1):
                AutomationAction.objects.create(
                    automation_id=automation_id,
                    trigger_id=trigger.pk,
                    order=order,
                    action_type=action.action_type,
                    device_id=action.device_id,
                    function=action.function,
                    parameter=action.parameter,
                    sequence_id=action.sequence_id,
                    delay=action.delay,
                )
        AutomationAction.objects.filter(
            automation_id=automation_id,
            trigger_id__isnull=True,
        ).delete()


def reverse_step_data(apps, schema_editor):
    # Legacy columns remain on SequenceStep, so no destructive reverse copy is
    # required.  Child rows are removed automatically if the schema rolls back.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0023_alter_automationcondition_condition_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="sequencestep",
            name="condition_operator",
            field=models.CharField(
                choices=[
                    ("and", "모든 조건 만족 (AND)"),
                    ("or", "하나 이상 만족 (OR)"),
                ],
                default="and",
                max_length=3,
            ),
        ),
        migrations.AlterField(
            model_name="sequencestep",
            name="device",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="iotcore.device",
            ),
        ),
        migrations.AlterField(
            model_name="sequencestep",
            name="function",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AlterField(
            model_name="automationtrigger",
            name="trigger_type",
            field=models.CharField(
                choices=[
                    ("set", "자동화 Step"),
                    ("time", "예약 시간 (기존)"),
                    ("mqtt_event", "MQTT 이벤트 (기존)"),
                    ("device_state", "기기 상태 변화 (기존)"),
                ],
                default="set",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="SequenceStepAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=1)),
                ("function", models.CharField(max_length=100)),
                ("parameter", models.JSONField(blank=True, null=True)),
                ("delay", models.PositiveIntegerField(default=0)),
                ("delay_position", models.CharField(choices=[("before", "동작 전"), ("after", "동작 후")], default="after", max_length=10)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sequence_step_actions", to="iotcore.device")),
                ("step", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="actions", to="iotcore.sequencestep")),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.CreateModel(
            name="SequenceStepCondition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("condition_type", models.CharField(choices=[("schedule", "날짜 / 시간"), ("time_window", "시간대 (기존)"), ("device_state", "기기 상태"), ("mqtt_event", "MQTT 이벤트"), ("weather", "현재 날씨"), ("event_value", "이벤트 데이터 (기존)")], max_length=20)),
                ("config", models.JSONField(default=dict)),
                ("order", models.PositiveIntegerField(default=1)),
                ("step", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conditions", to="iotcore.sequencestep")),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.AddConstraint(
            model_name="sequencestepaction",
            constraint=models.UniqueConstraint(fields=("step", "order"), name="unique_sequence_step_action_order"),
        ),
        migrations.AddConstraint(
            model_name="sequencestepcondition",
            constraint=models.UniqueConstraint(fields=("step", "order"), name="unique_sequence_step_condition_order"),
        ),
        migrations.RunPython(migrate_step_data, reverse_step_data),
    ]

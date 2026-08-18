import copy

import django.db.models.deletion
from django.db import migrations, models


def migrate_trigger_rows_to_condition_sets(apps, schema_editor):
    """Collapse v3's separate trigger source into the set's own conditions.

    v3 shape:
        AutomationTrigger(source) -> AutomationAction -> conditions

    v4 shape:
        AutomationTrigger(set: AND/OR) -> conditions -> one AutomationAction

    Device-state triggers that already have a condition on the same device do
    *not* get a redundant "any state changed" condition.  This intentionally
    fixes the UI/runtime duplication where, for example, Aqara T1 was selected
    once as the watcher and again as ``temperature < 24``.  The temperature
    condition itself becomes both the watched source and the predicate.
    """
    AutomationTrigger = apps.get_model("iotcore", "AutomationTrigger")
    AutomationAction = apps.get_model("iotcore", "AutomationAction")
    AutomationCondition = apps.get_model("iotcore", "AutomationCondition")

    def same_device(left, right):
        left = left or {}
        right = right or {}
        if left.get("device_id") and right.get("device_id"):
            return str(left.get("device_id")) == str(right.get("device_id"))
        if left.get("device_uid") and right.get("device_uid"):
            return str(left.get("device_uid")) == str(right.get("device_uid"))
        if left.get("topic") and right.get("topic"):
            return str(left.get("topic")) == str(right.get("topic"))
        return False

    for trigger in AutomationTrigger.objects.all().iterator():
        legacy_type = trigger.trigger_type
        legacy_config = copy.deepcopy(trigger.config or {})
        action = AutomationAction.objects.filter(trigger_id=trigger.pk).first()

        moved_conditions = []
        if action is not None:
            moved_conditions = list(
                AutomationCondition.objects.filter(action_id=action.pk)
                .order_by("order", "id")
            )
            for condition in moved_conditions:
                condition.trigger_id = trigger.pk
                condition.action_id = None
                condition.save(update_fields=["trigger", "action"])

        if legacy_type == "time":
            AutomationCondition.objects.create(
                automation_id=trigger.automation_id,
                trigger_id=trigger.pk,
                action_id=None,
                condition_type="schedule",
                config=legacy_config,
                order=0,
            )

        elif legacy_type == "mqtt_event":
            topic = legacy_config.get("topic") or ""
            converted_event_values = False
            for condition in moved_conditions:
                if condition.condition_type != "event_value":
                    continue
                config = copy.deepcopy(condition.config or {})
                config["topic"] = topic
                condition.condition_type = "mqtt_event"
                condition.config = config
                condition.save(update_fields=["condition_type", "config"])
                converted_event_values = True

            inline_filter = any(
                key in legacy_config for key in ("field", "operator", "value")
            )
            if inline_filter or not converted_event_values:
                config = {
                    "topic": topic,
                    "field": legacy_config.get("field") or "value",
                    "operator": (
                        legacy_config.get("operator")
                        if inline_filter else "received"
                    ),
                    "value": (
                        legacy_config.get("value")
                        if inline_filter else None
                    ),
                }
                AutomationCondition.objects.create(
                    automation_id=trigger.automation_id,
                    trigger_id=trigger.pk,
                    action_id=None,
                    condition_type="mqtt_event",
                    config=config,
                    order=0,
                )

        elif legacy_type == "device_state":
            # If a real condition already targets the watched device, that
            # condition becomes the source. Otherwise preserve the legacy
            # "any field changed" event as an explicit wildcard condition.
            has_same_device_condition = any(
                condition.condition_type == "device_state"
                and same_device(condition.config, legacy_config)
                and bool((condition.config or {}).get("key"))
                for condition in moved_conditions
            )
            if not has_same_device_condition:
                config = copy.deepcopy(legacy_config)
                config.update({"key": "*", "operator": "changed", "value": None})
                AutomationCondition.objects.create(
                    automation_id=trigger.automation_id,
                    trigger_id=trigger.pk,
                    action_id=None,
                    condition_type="device_state",
                    config=config,
                    order=0,
                )

        # SET rows are the canonical representation from this point forward.
        trigger.trigger_type = "set"
        trigger.config = {}
        trigger.condition_operator = "and"
        trigger.last_result = False
        trigger.save(update_fields=[
            "trigger_type",
            "config",
            "condition_operator",
            "last_result",
        ])


def reverse_noop(apps, schema_editor):
    # A condition-driven AND/OR set cannot be faithfully collapsed back into
    # one external trigger source, so rollback keeps the data as-is.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0019_trigger_scoped_execution_sets"),
    ]

    operations = [
        migrations.AddField(
            model_name="automationtrigger",
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
        migrations.AddField(
            model_name="automationtrigger",
            name="last_result",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="automationcondition",
            name="trigger",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="conditions",
                to="iotcore.automationtrigger",
            ),
        ),
        migrations.AlterField(
            model_name="automationtrigger",
            name="trigger_type",
            field=models.CharField(
                choices=[
                    ("set", "트리거 세트"),
                    ("time", "예약 시간 (기존)"),
                    ("mqtt_event", "MQTT 이벤트 (기존)"),
                    ("device_state", "기기 상태 변화 (기존)"),
                ],
                default="set",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="automationcondition",
            name="condition_type",
            field=models.CharField(
                choices=[
                    ("schedule", "예약 시간"),
                    ("time_window", "시간대"),
                    ("device_state", "기기 상태"),
                    ("mqtt_event", "MQTT 이벤트"),
                    ("event_value", "트리거 데이터 (기존)"),
                ],
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="automationcondition",
            name="unique_automation_action_condition_order",
        ),
        migrations.RunPython(
            migrate_trigger_rows_to_condition_sets,
            reverse_noop,
        ),
        migrations.AddConstraint(
            model_name="automationcondition",
            constraint=models.UniqueConstraint(
                fields=("trigger", "order"),
                name="unique_automation_trigger_condition_order",
            ),
        ),
    ]

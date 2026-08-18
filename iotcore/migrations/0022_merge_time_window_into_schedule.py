from django.db import migrations


ALL_WEEKDAYS = list(range(7))


def _same_owner_schedule(AutomationCondition, condition):
    queryset = AutomationCondition.objects.filter(
        automation_id=condition.automation_id,
        condition_type="schedule",
    )
    if condition.trigger_id:
        return queryset.filter(trigger_id=condition.trigger_id).first()
    if condition.action_id:
        return queryset.filter(
            trigger_id__isnull=True,
            action_id=condition.action_id,
        ).first()
    return queryset.filter(
        trigger_id__isnull=True,
        action_id__isnull=True,
    ).first()


def forwards(apps, schema_editor):
    AutomationCondition = apps.get_model("iotcore", "AutomationCondition")

    # Canonicalize existing weekly point schedules so every edited row has an
    # explicit time mode after this migration.
    for condition in AutomationCondition.objects.filter(condition_type="schedule"):
        config = dict(condition.config or {})
        if (
            config.get("schedule_type") == "weekly"
            and not config.get("time_mode")
        ):
            config["time_mode"] = "at"
            condition.config = config
            condition.save(update_fields=["config"])

    for condition in AutomationCondition.objects.filter(condition_type="time_window"):
        # A TriggerSet may already own a reservation-time condition. Because
        # the new editor intentionally allows only one reservation-time block,
        # do not guess how two independent legacy time predicates should merge.
        # Such rare rows remain editable as "시간대 (기존)".
        if _same_owner_schedule(AutomationCondition, condition) is not None:
            continue

        config = dict(condition.config or {})
        start = config.get("start")
        end = config.get("end")
        if not start:
            continue
        weekdays = config.get("weekdays") or ALL_WEEKDAYS
        condition.condition_type = "schedule"
        condition.config = {
            "schedule_type": "weekly",
            "time_mode": "window",
            "weekdays": list(weekdays),
            "start": start,
            "end": end or None,
        }
        condition.save(update_fields=["condition_type", "config"])


def backwards(apps, schema_editor):
    AutomationCondition = apps.get_model("iotcore", "AutomationCondition")
    for condition in AutomationCondition.objects.filter(condition_type="schedule"):
        config = dict(condition.config or {})
        if (
            config.get("schedule_type") == "weekly"
            and config.get("time_mode") == "window"
        ):
            condition.condition_type = "time_window"
            condition.config = {
                "start": config.get("start"),
                "end": config.get("end"),
                "weekdays": config.get("weekdays") or ALL_WEEKDAYS,
            }
            condition.save(update_fields=["condition_type", "config"])
            continue
        if (
            config.get("schedule_type") == "weekly"
            and config.get("time_mode") == "at"
        ):
            config.pop("time_mode", None)
            condition.config = config
            condition.save(update_fields=["config"])


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0021_trigger_set_multiple_actions"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

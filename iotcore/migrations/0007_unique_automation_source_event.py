from django.db import migrations, models


def remove_duplicate_source_events(apps, schema_editor):
    SequenceRun = apps.get_model("iotcore", "SequenceRun")
    seen = set()
    duplicate_ids = []

    rows = (
        SequenceRun.objects
        .exclude(automation_id__isnull=True)
        .exclude(source_event_id__isnull=True)
        .exclude(source_event_id="")
        .order_by("id")
        .values_list("id", "automation_id", "source_event_id")
    )
    for run_id, automation_id, source_event_id in rows:
        key = (automation_id, source_event_id)
        if key in seen:
            duplicate_ids.append(run_id)
        else:
            seen.add(key)

    if duplicate_ids:
        SequenceRun.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0006_automation_triggers_conditions"),
    ]

    operations = [
        migrations.RunPython(
            remove_duplicate_source_events,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="sequencerun",
            constraint=models.UniqueConstraint(
                fields=("automation", "source_event_id"),
                name="unique_automation_source_event",
            ),
        ),
    ]

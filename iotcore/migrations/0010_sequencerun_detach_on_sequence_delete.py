import django.db.models.deletion
from django.db import migrations, models


def backfill_sequence_names(apps, schema_editor):
    SequenceRun = apps.get_model("iotcore", "SequenceRun")

    batch = []
    runs = (
        SequenceRun.objects
        .filter(sequence_name="")
        .exclude(sequence_id=None)
        .select_related("sequence")
        .iterator(chunk_size=500)
    )
    for run in runs:
        run.sequence_name = run.sequence.name
        batch.append(run)
        if len(batch) >= 500:
            SequenceRun.objects.bulk_update(batch, ["sequence_name"])
            batch.clear()

    if batch:
        SequenceRun.objects.bulk_update(batch, ["sequence_name"])


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0009_devicestate_key_max_length"),
    ]

    operations = [
        migrations.AddField(
            model_name="sequencerun",
            name="sequence_name",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.RunPython(
            backfill_sequence_names,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="sequencerun",
            name="sequence",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="runs",
                to="iotcore.sequence",
            ),
        ),
    ]

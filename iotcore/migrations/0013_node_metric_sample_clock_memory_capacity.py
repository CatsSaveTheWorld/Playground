from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0012_node_metric_sample"),
    ]

    operations = [
        migrations.AddField(
            model_name="nodemetricsample",
            name="cpu_current_ghz",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="nodemetricsample",
            name="cpu_max_ghz",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="nodemetricsample",
            name="memory_total_gb",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="nodemetricsample",
            name="memory_used_gb",
            field=models.FloatField(blank=True, null=True),
        ),
    ]

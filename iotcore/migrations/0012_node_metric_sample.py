import django.db.models.deletion
from django.db import migrations, models


def ensure_ai_monitor_device(apps, schema_editor):
    Device = apps.get_model("iotcore", "Device")
    Device.objects.get_or_create(
        device_uid="home-ai-main",
        defaults={
            "name": "AI 추론 PC",
            "device_type": "pc",
            "protocol": "mqtt",
            "location": "이도원 방",
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0011_rename_automation_display_labels"),
    ]

    operations = [
        migrations.CreateModel(
            name="NodeMetricSample",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("cpu_percent", models.FloatField()),
                ("memory_percent", models.FloatField()),
                ("download_mbps", models.FloatField()),
                ("upload_mbps", models.FloatField()),
                ("storage_percent", models.FloatField(blank=True, null=True)),
                ("storage_used_gb", models.FloatField(blank=True, null=True)),
                ("storage_total_gb", models.FloatField(blank=True, null=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metric_samples",
                        to="iotcore.device",
                    ),
                ),
            ],
            options={
                "ordering": ["-recorded_at"],
                "indexes": [
                    models.Index(
                        fields=["device", "-recorded_at"],
                        name="iotcore_node_device_time_idx",
                    )
                ],
            },
        ),
        migrations.RunPython(
            ensure_ai_monitor_device,
            migrations.RunPython.noop,
        ),
    ]

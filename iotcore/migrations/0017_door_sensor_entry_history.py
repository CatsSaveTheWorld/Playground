import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


DOOR_SENSOR_UID = "livingroom_door_sensor"


def register_door_sensor(apps, schema_editor):
    Device = apps.get_model("iotcore", "Device")
    device, created = Device.objects.get_or_create(
        device_uid=DOOR_SENSOR_UID,
        defaults={
            "device_type": "door_sensor",
            "device_role": "sensor",
            "protocol": "zigbee",
            "name": "방문 센서",
            "location": "내 방",
        },
    )

    # Preserve the user's display name/location if the row already exists,
    # but normalize the technical fields needed by IoTCore.
    changed = []
    if device.device_type != "door_sensor":
        device.device_type = "door_sensor"
        changed.append("device_type")
    if device.device_role != "sensor":
        device.device_role = "sensor"
        changed.append("device_role")
    if device.protocol != "zigbee":
        device.protocol = "zigbee"
        changed.append("protocol")
    if changed:
        device.save(update_fields=changed)


def unregister_door_sensor(apps, schema_editor):
    # Do not delete a physical device record on migration rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0016_sequence_automation_groups_favorites"),
    ]

    operations = [
        migrations.CreateModel(
            name="DoorEvent",
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
                ("is_open", models.BooleanField(db_index=True)),
                (
                    "recorded_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                    ),
                ),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="door_events",
                        to="iotcore.device",
                    ),
                ),
            ],
            options={
                "ordering": ["-recorded_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="doorevent",
            index=models.Index(
                fields=["device", "-recorded_at"],
                name="iotcore_door_device_time_idx",
            ),
        ),
        migrations.RunPython(register_door_sensor, unregister_door_sensor),
    ]

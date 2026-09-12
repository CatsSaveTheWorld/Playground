from django.db import migrations, models


PC_DEVICES = (
    {
        "device_uid": "home-ai-main",
        "name": "Home-AI-Main",
        "location": "내 방",
        "control_config": {
            "mac_address": "10:FF:E0:38:16:BB",
            "ip_address": "192.168.0.4",
            "wol_broadcast_ip": "255.255.255.255",
            "wol_port": 9,
            "agent_port": 5050,
            "agent_path": "/shutdown",
        },
    },
    {
        "device_uid": "home-ai-sub1",
        "name": "Home-AI-Sub1",
        "location": "내 방",
        "control_config": {
            "mac_address": "A8-A1-59-20-E5-E6",
            "ip_address": "192.168.0.2",
            "wol_broadcast_ip": "255.255.255.255",
            "wol_port": 10,
            "agent_port": 5050,
            "agent_path": "/shutdown",
        },
    },
)


def migrate_pc_devices(apps, schema_editor):
    Device = apps.get_model("iotcore", "Device")
    for row in PC_DEVICES:
        device, created = Device.objects.get_or_create(
            device_uid=row["device_uid"],
            defaults={
                "device_type": "pc",
                "device_role": "hybrid",
                "protocol": "tcpip",
                "name": row["name"],
                "location": row["location"],
                "control_config": row["control_config"],
            },
        )
        if created:
            continue

        changed = []
        # A pre-existing telemetry Device is promoted to the canonical PC Device.
        if device.device_type != "pc":
            device.device_type = "pc"
            changed.append("device_type")
        if device.device_role != "hybrid":
            device.device_role = "hybrid"
            changed.append("device_role")
        if device.protocol != "tcpip":
            device.protocol = "tcpip"
            changed.append("protocol")
        # These two rows replace the legacy Computers.csv entries, so keep the
        # user-facing names/location identical to the former PC cards.
        if device.name != row["name"]:
            device.name = row["name"]
            changed.append("name")
        if device.location != row["location"]:
            device.location = row["location"]
            changed.append("location")

        config = dict(device.control_config or {})
        merged = dict(row["control_config"])
        merged.update(config)  # keep any values already customized in DB
        if merged != (device.control_config or {}):
            device.control_config = merged
            changed.append("control_config")
        if changed:
            device.save(update_fields=changed)


def noop_reverse(apps, schema_editor):
    # Do not delete Device rows on reverse: they may already own telemetry/history.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0026_alter_automationrun_options_alter_actionrun_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="control_config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(migrate_pc_devices, noop_reverse),
    ]

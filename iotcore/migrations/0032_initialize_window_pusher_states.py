from django.db import migrations


WINDOW_PUSHER_STATE_DEFAULTS = {
    "online": None,
    "state": None,
    "position": None,
    "window_state": None,
    "motion_direction": None,
    "target_position": None,
    "battery": None,
    "charging": None,
    "linkquality": None,
    "automatic_mode": None,
    "slow_stop": None,
    "button_position": None,
    "last_command": None,
}


def initialize_window_pusher_states(apps, schema_editor):
    Device = apps.get_model("iotcore", "Device")
    DeviceState = apps.get_model("iotcore", "DeviceState")

    for device in Device.objects.filter(device_type="window_pusher").iterator():
        # A window pusher is both controllable and a state source (position,
        # battery, online, charging), so expose it to both Action and Trigger
        # editors.
        if device.device_role != "hybrid":
            device.device_role = "hybrid"
            device.save(update_fields=["device_role"])

        topic = f"iotcore/devices/{device.device_uid}/state"
        for key, default in WINDOW_PUSHER_STATE_DEFAULTS.items():
            DeviceState.objects.get_or_create(
                topic=topic,
                key=key,
                defaults={"value": default},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0031_remove_actionrun_scheduled_for"),
    ]

    operations = [
        migrations.RunPython(
            initialize_window_pusher_states,
            migrations.RunPython.noop,
        ),
    ]

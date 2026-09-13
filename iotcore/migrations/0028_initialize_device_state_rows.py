from django.db import migrations


# Snapshot of the runtime state-schema defaults at the time this migration was
# created.  Keep migrations self-contained so future schema edits do not change
# historical migration behavior.
STATE_DEFAULTS_BY_DEVICE_TYPE = {
    "aircon": {
        "power": None,
        "mode": None,
        "target_temperature": None,
    },
    "fan": {
        "power": None,
    },
    "electric_fan": {
        "power": None,
        "speed": None,
        "vertical_swing": None,
        "horizontal_swing": None,
        "horizontal_angle": None,
        "beep": None,
    },
    "light": {
        "power": None,
    },
    "projector": {
        "power": None,
    },
    "pc": {
        "power": None,
        "online": None,
    },
    "speaker": {
        "playback_state": None,
        "volume": None,
        "shuffle": None,
        "repeat_mode": None,
    },
    "media_server": {
        "online": None,
    },
    "media_node": {
        "online": None,
    },
    "door_sensor": {
        "contact": None,
        "battery": None,
        "linkquality": None,
    },
    "motion_sensor": {
        "occupancy": None,
        "illuminance": None,
        "battery": None,
        "linkquality": None,
        "zone1": None,
        "zone2": None,
        "zone3": None,
        "zone4": None,
        "zone5": None,
        "zone6": None,
        "zone7": None,
    },
    "temp_humidity": {
        "temperature": None,
        "humidity": None,
        "pressure": None,
        "battery": None,
        "linkquality": None,
    },
    "temperature_humidity": {
        "temperature": None,
        "humidity": None,
        "pressure": None,
        "battery": None,
        "linkquality": None,
    },
    "temperature_humidity_sensor": {
        "temperature": None,
        "humidity": None,
        "pressure": None,
        "battery": None,
        "linkquality": None,
    },
}

CONTROLLER_DEFAULTS = {
    "controller_online": None,
    "controller_last_command": None,
}

NETWORK_ENDPOINT_KEYS = ("status_ip", "ip_address", "host")


def initialize_device_state_rows(apps, schema_editor):
    Device = apps.get_model("iotcore", "Device")
    Controller = apps.get_model("iotcore", "Controller")
    DeviceState = apps.get_model("iotcore", "DeviceState")

    controller_device_ids = set(
        Controller.objects.exclude(device_id=None).values_list("device_id", flat=True)
    )

    for device in Device.objects.all().iterator():
        defaults = dict(STATE_DEFAULTS_BY_DEVICE_TYPE.get(device.device_type, {}))

        config = dict(device.control_config or {})
        if any(str(config.get(key) or "").strip() for key in NETWORK_ENDPOINT_KEYS):
            defaults.setdefault("online", None)

        if device.pk in controller_device_ids:
            for key, value in CONTROLLER_DEFAULTS.items():
                defaults.setdefault(key, value)

        if not defaults:
            continue

        topic = f"iotcore/devices/{device.device_uid}/state"
        for key, value in defaults.items():
            DeviceState.objects.get_or_create(
                topic=topic,
                key=key,
                defaults={"value": value},
            )


def noop_reverse(apps, schema_editor):
    # These rows may have acquired real runtime values after this migration.
    # Deleting them on reverse would destroy state/history information.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0027_device_control_config_pc_devices"),
    ]

    operations = [
        migrations.RunPython(initialize_device_state_rows, noop_reverse),
    ]

from django.apps import AppConfig


class IoTCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "iotcore"

    def ready(self):
        # Register Device/Controller post-save handlers that create missing
        # canonical DeviceState rows for newly added or reassigned devices.
        from . import signals  # noqa: F401

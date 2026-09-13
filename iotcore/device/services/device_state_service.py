"""Shared DeviceState creation/update helpers.

All callers should use this service instead of manually constructing canonical
DeviceState rows.  It keeps development/production behavior identical and makes
state initialization idempotent.
"""

from __future__ import annotations

from ...models import Controller, Device, DeviceState
from ..state_schema import (
    CONTROLLER_STATE_SCHEMA,
    DEVICE_STATE_SCHEMA,
    NETWORK_ENDPOINT_KEYS,
    NETWORK_STATE_SCHEMA,
)


class DeviceStateService:
    @staticmethod
    def canonical_topic(device: Device) -> str:
        return f"iotcore/devices/{device.device_uid}/state"

    @classmethod
    def definitions_for(cls, device: Device) -> dict[str, dict]:
        """Return the state definitions currently applicable to ``device``."""
        definitions = {
            key: dict(spec)
            for key, spec in DEVICE_STATE_SCHEMA.get(device.device_type, {}).items()
        }

        config = dict(device.control_config or {})
        if any(str(config.get(key) or "").strip() for key in NETWORK_ENDPOINT_KEYS):
            for key, spec in NETWORK_STATE_SCHEMA.items():
                definitions.setdefault(key, dict(spec))

        if device.pk and Controller.objects.filter(device_id=device.pk).exists():
            for key, spec in CONTROLLER_STATE_SCHEMA.items():
                definitions.setdefault(key, dict(spec))

        return definitions

    @classmethod
    def ensure_device_states(cls, device: Device) -> list[DeviceState]:
        """Create missing canonical state rows without overwriting known values."""
        if device is None or not device.pk:
            return []

        topic = cls.canonical_topic(device)
        rows = []
        for key, spec in cls.definitions_for(device).items():
            state, _created = DeviceState.objects.get_or_create(
                topic=topic,
                key=key,
                defaults={"value": spec.get("default")},
            )
            rows.append(state)
        return rows

    @classmethod
    def ensure_all_device_states(cls) -> int:
        """Idempotently initialize all currently registered Devices."""
        created_before = DeviceState.objects.count()
        for device in Device.objects.all().iterator():
            cls.ensure_device_states(device)
        return DeviceState.objects.count() - created_before

    @classmethod
    def set_state(cls, device: Device, key: str, value) -> DeviceState:
        state, _created = DeviceState.objects.update_or_create(
            topic=cls.canonical_topic(device),
            key=str(key),
            defaults={"value": value},
        )
        return state

    @classmethod
    def set_states(cls, device: Device, values: dict) -> dict[str, DeviceState]:
        return {
            str(key): cls.set_state(device, str(key), value)
            for key, value in (values or {}).items()
        }

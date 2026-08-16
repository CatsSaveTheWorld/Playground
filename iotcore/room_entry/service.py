from datetime import datetime, time

from django.conf import settings
from django.utils import timezone

from ..models import Device, DeviceState, DoorEvent


class RoomEntryService:
    """Aggregate the room door sensor state for the dashboard and history."""

    DEFAULT_DEVICE_UID = "livingroom_door_sensor"

    @classmethod
    def device_uid(cls):
        return getattr(
            settings,
            "IOTCORE_DOOR_SENSOR_UID",
            cls.DEFAULT_DEVICE_UID,
        )

    @classmethod
    def _door_device(cls):
        return Device.objects.filter(device_uid=cls.device_uid()).first()

    @staticmethod
    def _coerce_contact(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "on", "closed"}:
                return True
            if normalized in {"false", "0", "off", "open"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return None

    @classmethod
    def _state(cls, device, key):
        if device is None:
            return None

        canonical_topic = f"iotcore/devices/{device.device_uid}/state"
        state = DeviceState.objects.filter(
            topic=canonical_topic,
            key=key,
        ).first()
        if state is not None:
            return state

        return DeviceState.objects.filter(
            topic=f"zigbee2mqtt/{device.device_uid}",
            key=key,
        ).first()

    @classmethod
    def snapshot(cls):
        device = cls._door_device()
        if device is None:
            return {
                "connected": False,
                "device_uid": cls.device_uid(),
                "current_state": None,
                "is_open": None,
                "open_count": None,
                "attempt_count": None,
                "last_event_at": None,
                "battery": None,
                "linkquality": None,
                "video_url": getattr(settings, "IOTCORE_ENTRY_VIDEO_URL", ""),
            }

        contact_state = cls._state(device, "contact")
        contact = cls._coerce_contact(
            contact_state.value if contact_state is not None else None
        )
        is_open = None if contact is None else not contact

        today = timezone.localdate()
        day_start = timezone.make_aware(
            datetime.combine(today, time.min),
            timezone.get_current_timezone(),
        )
        open_count = DoorEvent.objects.filter(
            device=device,
            is_open=True,
            recorded_at__gte=day_start,
        ).count()
        last_event = DoorEvent.objects.filter(device=device).first()
        battery_state = cls._state(device, "battery")
        linkquality_state = cls._state(device, "linkquality")

        return {
            "connected": contact_state is not None,
            "device_uid": device.device_uid,
            "current_state": (
                "문 열림" if is_open is True
                else "문 닫힘" if is_open is False
                else None
            ),
            "is_open": is_open,
            "open_count": open_count,
            # A contact sensor cannot distinguish an unsuccessful opening attempt.
            "attempt_count": None,
            "last_event_at": last_event.recorded_at if last_event else None,
            "battery": battery_state.value if battery_state is not None else None,
            "linkquality": (
                linkquality_state.value if linkquality_state is not None else None
            ),
            "video_url": getattr(settings, "IOTCORE_ENTRY_VIDEO_URL", ""),
        }

    @classmethod
    def record_contact_change(
        cls,
        *,
        device,
        payload,
        previous,
        changed_keys,
        now=None,
    ):
        """Persist a door open/close transition from a live device event."""
        if device is None:
            return None
        if device.device_uid != cls.device_uid() and device.device_type != "door_sensor":
            return None
        if "contact" not in changed_keys or "contact" not in payload:
            return None
        if "contact" not in previous:
            # First observation establishes a baseline; it is not a door event.
            return None

        previous_contact = cls._coerce_contact(previous.get("contact"))
        current_contact = cls._coerce_contact(payload.get("contact"))
        if (
            previous_contact is None
            or current_contact is None
            or previous_contact == current_contact
        ):
            return None

        return DoorEvent.objects.create(
            device=device,
            is_open=not current_contact,
            recorded_at=now or timezone.now(),
        )

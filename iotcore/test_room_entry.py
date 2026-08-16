from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Device, DeviceState, DoorEvent
from .room_entry.service import RoomEntryService
from .scheduler.service import AutomationService


@override_settings(IOTCORE_DOOR_SENSOR_UID="livingroom_door_sensor")
class RoomEntryServiceTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="livingroom_door_sensor",
            device_type="door_sensor",
            device_role=Device.Role.SENSOR,
            protocol=Device.Protocol.ZIGBEE,
            name="방문 센서",
            location="내 방",
        )
        self.topic = "zigbee2mqtt/livingroom_door_sensor"
        self.canonical_topic = AutomationService.canonical_state_topic(self.device)

    def test_snapshot_maps_contact_true_to_closed(self):
        DeviceState.objects.create(
            topic=self.canonical_topic,
            key="contact",
            value=True,
        )

        snapshot = RoomEntryService.snapshot()

        self.assertTrue(snapshot["connected"])
        self.assertFalse(snapshot["is_open"])
        self.assertEqual(snapshot["current_state"], "문 닫힘")
        self.assertEqual(snapshot["open_count"], 0)

    def test_snapshot_maps_contact_false_to_open(self):
        DeviceState.objects.create(
            topic=self.topic,
            key="contact",
            value=False,
        )

        snapshot = RoomEntryService.snapshot()

        self.assertTrue(snapshot["connected"])
        self.assertTrue(snapshot["is_open"])
        self.assertEqual(snapshot["current_state"], "문 열림")

    def test_live_contact_changes_create_history_only_on_transition(self):
        # First observation establishes a baseline and must not count as an event.
        AutomationService.process_event(
            self.topic,
            {"contact": True, "linkquality": 240},
        )
        self.assertEqual(DoorEvent.objects.count(), 0)

        AutomationService.process_event(
            self.topic,
            {"contact": False, "linkquality": 244},
        )
        self.assertEqual(DoorEvent.objects.count(), 1)
        self.assertTrue(DoorEvent.objects.first().is_open)

        # Link-quality-only changes must not create duplicate door events.
        AutomationService.process_event(
            self.topic,
            {"contact": False, "linkquality": 255},
        )
        self.assertEqual(DoorEvent.objects.count(), 1)

        AutomationService.process_event(
            self.topic,
            {"contact": True, "linkquality": 252},
        )
        self.assertEqual(DoorEvent.objects.count(), 2)
        self.assertFalse(DoorEvent.objects.first().is_open)

    def test_snapshot_counts_only_today_open_events(self):
        DoorEvent.objects.create(device=self.device, is_open=True)
        DoorEvent.objects.create(device=self.device, is_open=False)
        old = DoorEvent.objects.create(device=self.device, is_open=True)
        DoorEvent.objects.filter(pk=old.pk).update(
            recorded_at=timezone.now() - timedelta(days=2)
        )

        snapshot = RoomEntryService.snapshot()

        self.assertEqual(snapshot["open_count"], 1)
        self.assertIsNotNone(snapshot["last_event_at"])
        self.assertIsNone(snapshot["attempt_count"])

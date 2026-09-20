from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .device.services.device_service import DeviceService
from .device.services.device_state_service import DeviceStateService
from .device.services.window_pusher_service import WindowPusherService
from .models import ActionRun, AutomationRun, Device
from .scheduler.executor import AutomationExecutor
from .scheduler.service import AutomationService


class WindowPusherStateTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="test_room_window_pusher",
            device_type="window_pusher",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.ZIGBEE,
            name="테스트 창문",
            location="테스트 방",
        )
        DeviceStateService.ensure_device_states(self.device)

    def test_position_is_derived_into_window_state(self):
        self.assertEqual(
            WindowPusherService.state_from_position(self.device, 0),
            "closed",
        )
        self.assertEqual(
            WindowPusherService.state_from_position(self.device, 100),
            "open",
        )
        self.assertEqual(
            WindowPusherService.state_from_position(self.device, 50),
            "partial",
        )

    def test_position_mapping_can_be_reversed_per_device(self):
        self.device.control_config = {
            "closed_position": 100,
            "open_position": 0,
            "position_tolerance": 5,
        }
        self.device.save(update_fields=["control_config"])

        self.assertEqual(
            WindowPusherService.state_from_position(self.device, 98),
            "closed",
        )
        self.assertEqual(
            WindowPusherService.state_from_position(self.device, 3),
            "open",
        )
        self.assertEqual(
            WindowPusherService.state_from_position(self.device, 50),
            "partial",
        )

    def test_zigbee_report_updates_canonical_position_battery_and_state(self):
        AutomationService.update_device_state(
            f"zigbee2mqtt/{self.device.device_uid}",
            {
                "position": 100,
                "battery": 87,
                "charging": False,
                "linkquality": 255,
            },
        )

        self.assertEqual(DeviceStateService.get_value(self.device, "position"), 100)
        self.assertEqual(DeviceStateService.get_value(self.device, "window_state"), "open")
        self.assertEqual(DeviceStateService.get_value(self.device, "battery"), 87)
        self.assertIs(DeviceStateService.get_value(self.device, "charging"), False)

    def test_zigbee_availability_updates_online_without_overwriting_cover_state(self):
        DeviceStateService.set_state(self.device, "state", "OPEN")

        AutomationService.update_device_state(
            f"zigbee2mqtt/{self.device.device_uid}/availability",
            {"state": "online"},
        )

        self.assertIs(DeviceStateService.get_value(self.device, "online"), True)
        self.assertEqual(DeviceStateService.get_value(self.device, "state"), "OPEN")


class WindowPusherControlTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="test_room_window_pusher",
            device_type="window_pusher",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.ZIGBEE,
            name="테스트 창문",
            location="테스트 방",
            control_config={
                "left_command": "OPEN",
                "right_command": "CLOSE",
                "safety_stop_seconds": 15,
            },
        )
        DeviceStateService.ensure_device_states(self.device)

    @patch(
        "iotcore.device.services.window_pusher_service.ZigbeeClient.send_payload",
        return_value=(True, ""),
    )
    def test_left_move_publishes_open_and_schedules_durable_stop(self, send_payload):
        before = timezone.now()
        success, message = DeviceService.control(self.device.id, "move_left")

        self.assertTrue(success, message)
        send_payload.assert_called_once_with(
            self.device.device_uid,
            {"state": "OPEN"},
        )
        queued = ActionRun.objects.get(function="safety_stop")
        self.assertEqual(queued.device_id, self.device.id)
        self.assertEqual(queued.status, AutomationRun.Status.WAITING)
        self.assertTrue(queued.parameter["safety_stop"])
        self.assertGreaterEqual(queued.available_at, before + timedelta(seconds=14))
        self.assertLessEqual(queued.available_at, before + timedelta(seconds=16))
        self.assertEqual(
            DeviceStateService.get_value(self.device, "motion_direction"),
            "left",
        )

    @patch(
        "iotcore.device.services.window_pusher_service.ZigbeeClient.send_payload",
        return_value=(True, ""),
    )
    def test_new_motion_cancels_old_safety_stop(self, send_payload):
        DeviceService.control(self.device.id, "move_left")
        first = ActionRun.objects.get(function="safety_stop")

        DeviceService.control(self.device.id, "move_right")
        first.refresh_from_db()

        self.assertEqual(first.status, AutomationRun.Status.CANCELLED)
        active = ActionRun.objects.filter(
            function="safety_stop",
            status=AutomationRun.Status.WAITING,
        )
        self.assertEqual(active.count(), 1)
        self.assertEqual(send_payload.call_count, 2)

    @patch(
        "iotcore.device.services.window_pusher_service.ZigbeeClient.send_payload",
        return_value=(True, ""),
    )
    def test_manual_stop_cancels_pending_safety_stop(self, send_payload):
        DeviceService.control(self.device.id, "move_left")
        queued = ActionRun.objects.get(function="safety_stop")

        success, message = DeviceService.control(self.device.id, "stop")
        queued.refresh_from_db()

        self.assertTrue(success, message)
        self.assertEqual(queued.status, AutomationRun.Status.CANCELLED)
        self.assertEqual(send_payload.call_args_list[-1].args[1], {"state": "STOP"})
        self.assertIsNone(DeviceStateService.get_value(self.device, "motion_direction"))

    @patch(
        "iotcore.device.services.window_pusher_service.ZigbeeClient.send_payload",
        return_value=(True, ""),
    )
    def test_due_safety_stop_executes_through_automation_worker(self, send_payload):
        DeviceService.control(self.device.id, "move_left")
        queued = ActionRun.objects.get(function="safety_stop")
        queued.available_at = timezone.now() - timedelta(seconds=1)
        queued.save(update_fields=["available_at"])

        AutomationExecutor.run_next_pending()

        queued.refresh_from_db()
        queued.root_run.refresh_from_db()
        self.assertEqual(queued.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(queued.root_run.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(send_payload.call_count, 2)
        self.assertEqual(send_payload.call_args_list[-1].args[1], {"state": "STOP"})
        self.assertIsNone(DeviceStateService.get_value(self.device, "motion_direction"))

    @patch(
        "iotcore.device.services.window_pusher_service.WindowPusherService.replace_safety_stop",
        side_effect=RuntimeError("queue unavailable"),
    )
    @patch(
        "iotcore.device.services.window_pusher_service.ZigbeeClient.send_payload",
        return_value=(True, ""),
    )
    def test_queue_failure_immediately_sends_stop(self, send_payload, replace_stop):
        success, message = DeviceService.control(self.device.id, "move_left")

        self.assertFalse(success)
        self.assertIn("즉시 STOP", message)
        self.assertEqual(send_payload.call_count, 2)
        self.assertEqual(send_payload.call_args_list[0].args[1], {"state": "OPEN"})
        self.assertEqual(send_payload.call_args_list[1].args[1], {"state": "STOP"})
        replace_stop.assert_called_once_with(self.device)

    @patch(
        "iotcore.device.services.window_pusher_service.ZigbeeClient.send_payload",
        return_value=(True, ""),
    )
    def test_set_position_publishes_position_and_schedules_stop(self, send_payload):
        success, message = DeviceService.control(
            self.device.id,
            "set_position",
            window_position=35,
        )

        self.assertTrue(success, message)
        send_payload.assert_called_once_with(self.device.device_uid, {"position": 35})
        self.assertTrue(ActionRun.objects.filter(function="safety_stop").exists())
        self.assertEqual(DeviceStateService.get_value(self.device, "target_position"), 35)


class WindowPusherWebTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="window-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            device_uid="leedowon_living_room_window_pusher",
            device_type="window_pusher",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.ZIGBEE,
            name="거실 창문",
            location="거실",
        )
        DeviceStateService.ensure_device_states(self.device)
        DeviceStateService.set_state(self.device, "battery", 100)
        DeviceStateService.set_state(self.device, "position", 100)
        DeviceStateService.set_state(self.device, "window_state", "open")
        DeviceStateService.set_state(self.device, "online", True)

    def test_device_control_page_renders_window_pusher_controls(self):
        response = self.client.get(reverse("iotcore:device_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "거실 창문")
        self.assertContains(response, "← 왼쪽")
        self.assertContains(response, "■ 정지")
        self.assertContains(response, "오른쪽 →")
        self.assertContains(response, "배터리")
        self.assertContains(response, "15")

    def test_state_endpoint_returns_canonical_state(self):
        response = self.client.get(
            reverse("iotcore:window_pusher_state", args=[self.device.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["state"]
        self.assertEqual(payload["battery"], 100)
        self.assertEqual(payload["position"], 100)
        self.assertEqual(payload["window_state"], "open")
        self.assertIs(payload["online"], True)

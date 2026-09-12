from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .device.services.device_service import DeviceService
from .models import Action, Automation, Device, Step
from .scheduler.executor import AutomationExecutor


class PCDeviceControlTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="pc-device-test-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.pc = Device.objects.update_or_create(
            device_uid="pc-device-test",
            defaults={
                "device_type": "pc",
                "device_role": Device.Role.HYBRID,
                "protocol": Device.Protocol.TCPIP,
                "name": "Test PC",
                "location": "내 방",
                "control_config": {
                    "mac_address": "00:11:22:33:44:55",
                    "ip_address": "192.168.0.20",
                    "wol_broadcast_ip": "192.168.0.255",
                    "wol_port": 9,
                    "agent_port": 5050,
                    "agent_path": "/shutdown",
                },
            },
        )[0]

    @patch("iotcore.device.services.device_service.WOLClient.send_wol")
    def test_power_on_reads_mac_and_wol_settings_from_device_db(self, send_wol):
        success, message = DeviceService.control(self.pc.id, "power_on")

        self.assertTrue(success, message)
        send_wol.assert_called_once_with(
            "00:11:22:33:44:55",
            ip="192.168.0.255",
            port=9,
        )

    @override_settings(PC_AGENT_TOKEN="secret-token")
    @patch("iotcore.device.services.device_service.requests.post")
    def test_power_off_reads_agent_endpoint_from_device_db(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": "shutdown accepted"}
        post.return_value = response

        success, message = DeviceService.control(self.pc.id, "power_off")

        self.assertTrue(success, message)
        self.assertEqual(message, "shutdown accepted")
        post.assert_called_once_with(
            "http://192.168.0.20:5050/shutdown",
            headers={"X-Token": "secret-token"},
            timeout=2,
        )

    @patch("iotcore.device.services.device_service.WOLClient.send_wol")
    def test_pc_web_endpoint_uses_device_id_not_client_mac_or_ip(self, send_wol):
        response = self.client.post(
            reverse("iotcore:pc_power_on"),
            {
                "device_id": self.pc.id,
                # Deliberately wrong values must not become the source of truth.
                "pc_mac": "FF:FF:FF:FF:FF:FF",
                "pc_ip": "203.0.113.10",
            },
        )

        self.assertEqual(response.status_code, 200)
        send_wol.assert_called_once_with(
            "00:11:22:33:44:55",
            ip="192.168.0.255",
            port=9,
        )

    @patch("iotcore.device.services.device_service.WOLClient.send_wol")
    def test_pc_action_can_run_through_automation_executor(self, send_wol):
        automation = Automation.objects.create(
            name="PC Wake",
            automation_type=Automation.Type.IMMEDIATE,
        )
        step = Step.objects.create(automation=automation, order=1)
        Action.objects.create(
            step=step,
            order=1,
            action_type=Action.Type.DEVICE,
            device=self.pc,
            function="power_on",
        )

        success, message = AutomationExecutor.execute(automation)

        self.assertTrue(success, message)
        send_wol.assert_called_once()

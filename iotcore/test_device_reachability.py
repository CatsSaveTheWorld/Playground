from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .device.services.reachability_service import DeviceReachabilityService
from .models import Controller, Device, DeviceState


class DeviceReachabilityServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reachability-user",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_direct_network_device_uses_control_config_ip(self):
        device = Device.objects.create(
            device_type="pc",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.TCPIP,
            device_uid="reachability-pc",
            name="Reachability PC",
            location="내 방",
            control_config={"ip_address": "192.0.2.10"},
        )
        targets = DeviceReachabilityService.describe_targets(device)
        self.assertEqual(targets["device"]["host"], "192.0.2.10")
        self.assertIsNone(targets["controller"])

    def test_ir_device_exposes_controller_endpoint(self):
        device = Device.objects.create(
            device_type="aircon",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="reachability-aircon",
            name="Reachability Aircon",
            location="내 방",
        )
        Controller.objects.create(
            name="ESP32",
            mac_address="00:11:22:33:44:66",
            ip_address="192.0.2.20",
            location="내 방",
            device=device,
        )
        device = Device.objects.select_related("controller").get(pk=device.pk)
        targets = DeviceReachabilityService.describe_targets(device)
        self.assertIsNone(targets["device"])
        self.assertEqual(targets["controller"]["host"], "192.0.2.20")

    def test_projector_can_report_device_and_controller_separately(self):
        device = Device.objects.create(
            device_type="projector",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="reachability-projector",
            name="Projector",
            location="내 방",
            control_config={"status_ip": "192.0.2.30"},
        )
        Controller.objects.create(
            name="Projector ESP32",
            mac_address="00:11:22:33:44:77",
            ip_address="192.0.2.31",
            location="내 방",
            device=device,
        )
        device = Device.objects.select_related("controller").get(pk=device.pk)
        targets = DeviceReachabilityService.describe_targets(device)
        self.assertEqual(targets["device"]["host"], "192.0.2.30")
        self.assertEqual(targets["controller"]["host"], "192.0.2.31")

    @patch.object(DeviceReachabilityService, "check_endpoint")
    def test_status_api_returns_and_persists_device_and_controller_health(self, check_endpoint):
        device = Device.objects.create(
            device_type="projector",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="reachability-api-projector",
            name="API Projector",
            location="내 방",
            control_config={"status_ip": "192.0.2.40"},
        )
        Controller.objects.create(
            name="API ESP32",
            mac_address="00:11:22:33:44:88",
            ip_address="192.0.2.41",
            location="내 방",
            device=device,
        )
        check_endpoint.side_effect = [True, False]

        response = self.client.get(reverse("iotcore:device_status"), {"ids": str(device.id)})
        self.assertEqual(response.status_code, 200)
        payload = response.json()["devices"][str(device.id)]
        self.assertIsNotNone(payload["device"])
        self.assertIsNotNone(payload["controller"])
        self.assertEqual(
            set(
                DeviceState.objects.filter(
                    topic=f"iotcore/devices/{device.device_uid}/state"
                ).values_list("key", flat=True)
            ),
            {"online", "controller_online"},
        )

    def test_device_control_contains_status_slots(self):
        device = Device.objects.create(
            device_type="pc",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.TCPIP,
            device_uid="reachability-card-pc",
            name="Card PC",
            location="내 방",
            control_config={"ip_address": "192.0.2.50"},
        )
        response = self.client.get(reverse("iotcore:device_control"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-device-status="{device.id}"')
        self.assertContains(response, reverse("iotcore:device_status"))

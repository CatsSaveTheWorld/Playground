from django.test import TestCase

from .device.services.device_state_service import DeviceStateService
from .models import Controller, Device, DeviceState


class DeviceStateInitializationTests(TestCase):
    def state_values(self, device):
        topic = DeviceStateService.canonical_topic(device)
        return {
            row.key: row.value
            for row in DeviceState.objects.filter(topic=topic)
        }

    def test_new_projector_gets_unknown_power_without_manual_shell(self):
        device = Device.objects.create(
            device_type="projector",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="test-projector",
            name="Test Projector",
            location="Test Room",
        )

        self.assertIn("power", self.state_values(device))
        self.assertIsNone(self.state_values(device)["power"])

    def test_assigning_controller_adds_controller_state_rows(self):
        device = Device.objects.create(
            device_type="projector",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="test-projector-controller",
            name="Test Projector",
            location="Test Room",
        )

        Controller.objects.create(
            name="Test ESP32",
            mac_address="00:11:22:33:44:55",
            ip_address="192.0.2.10",
            location="Test Room",
            device=device,
        )

        states = self.state_values(device)
        self.assertIn("controller_online", states)
        self.assertIsNone(states["controller_online"])
        self.assertIn("controller_last_command", states)
        self.assertIsNone(states["controller_last_command"])

    def test_reinitialization_never_overwrites_known_value(self):
        device = Device.objects.create(
            device_type="aircon",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="test-aircon",
            name="Test Aircon",
            location="Test Room",
        )
        DeviceStateService.set_state(device, "power", True)

        DeviceStateService.ensure_device_states(device)

        self.assertIs(
            DeviceState.objects.get(
                topic=DeviceStateService.canonical_topic(device),
                key="power",
            ).value,
            True,
        )

    def test_network_endpoint_adds_online_placeholder_for_any_device_type(self):
        device = Device.objects.create(
            device_type="custom_network_device",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.TCPIP,
            device_uid="test-custom-network",
            name="Custom Network Device",
            location="Test Room",
            control_config={"status_ip": "192.0.2.20"},
        )

        states = self.state_values(device)
        self.assertIn("online", states)
        self.assertIsNone(states["online"])

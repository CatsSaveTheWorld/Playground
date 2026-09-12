from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Device


class DeviceControlPlacementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="device-placement-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        Device.objects.filter(device_type="media_server").delete()

        self.pc = Device.objects.update_or_create(
            device_uid="home-ai-main",
            defaults={
                "device_type": "pc",
                "device_role": Device.Role.HYBRID,
                "protocol": Device.Protocol.TCPIP,
                "name": "Home-AI-Main",
                "location": "내 방",
                "control_config": {
                    "mac_address": "00:11:22:33:44:55",
                    "ip_address": "192.168.0.4",
                    "wol_port": 9,
                },
            },
        )[0]
        self.media_server = Device.objects.create(
            device_type="media_server",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.MQTT,
            device_uid="pi5-media-placement-test",
            name="미디어 서버",
            location="내 방",
        )

    def test_pc_and_media_server_are_room_devices(self):
        response = self.client.get(reverse("iotcore:device_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "컴퓨터")
        self.assertContains(response, "미디어")
        self.assertNotContains(response, "PC 제어")
        self.assertContains(response, "Home-AI-Main")
        self.assertContains(response, f'data-device-id="{self.pc.id}"')
        self.assertContains(response, "MEDIA SERVER")
        self.assertContains(response, self.media_server.name)
        self.assertContains(
            response,
            reverse("iotcore:media_server_control", args=[self.media_server.id]),
        )

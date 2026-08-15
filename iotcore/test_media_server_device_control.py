from unittest.mock import patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Device


class MediaServerDeviceControlPlacementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="media-placement-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        Device.objects.filter(device_type="media_server").delete()
        self.media_server = Device.objects.create(
            device_type="media_server",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.MQTT,
            device_uid="pi5-media-placement-test",
            name="미디어 서버",
            location="내 방",
        )

    @patch("iotcore.api.views.detail.pd.read_csv")
    def test_media_server_is_room_device_not_pc_card(self, read_csv):
        read_csv.return_value = pd.DataFrame(
            [
                ["Home-AI-Main", "00:11:22:33:44:55", "192.168.0.255", 9],
                ["파이", "AA:BB:CC:DD:EE:FF", "192.168.0.255", 9],
            ],
            columns=["name", "mac", "broadcast_ip", "port"],
        )

        response = self.client.get(reverse("iotcore:device_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Home-AI-Main")
        self.assertNotContains(response, 'data-pc-name="파이"')
        self.assertContains(response, "MEDIA SERVER")
        self.assertContains(response, "미디어 서버 제어")
        self.assertContains(
            response,
            reverse("iotcore:media_server_control", args=[self.media_server.id]),
        )

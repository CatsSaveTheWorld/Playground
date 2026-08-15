from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Device


class MediaServerControlTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="media-test-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            device_type="media_server",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.MQTT,
            device_uid="pi5-media-test",
            name="Pi5 미디어 서버",
            location="내 방",
        )

    def test_control_page_renders(self):
        response = self.client.get(
            reverse("iotcore:media_server_control", args=[self.device.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "영상 선택")
        self.assertContains(response, "분위기로 재생")

    @patch("iotcore.api.views.media_server.RemoteTaskClient.execute_result")
    def test_video_list_returns_agent_payload(self, execute_result):
        execute_result.return_value = {
            "success": True,
            "message": "영상 1개를 불러왔습니다.",
            "videos": [
                {
                    "id": "Mountain with Stars [2K 60FPS].mp4",
                    "title": "Mountain with Stars [2K 60FPS]",
                    "filename": "Mountain with Stars [2K 60FPS].mp4",
                    "relative_path": "Mountain with Stars [2K 60FPS].mp4",
                }
            ],
            "now_playing": None,
        }

        response = self.client.get(
            reverse("iotcore:media_server_videos", args=[self.device.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["videos"][0]["title"], "Mountain with Stars [2K 60FPS]")
        execute_result.assert_called_once_with(
            action="media.list_videos",
            parameters={},
            agent_id=self.device.device_uid,
            timeout=10,
        )

    @patch("iotcore.api.views.media_server.RemoteTaskClient.execute_result")
    def test_play_video_sends_only_relative_video_id(self, execute_result):
        execute_result.return_value = {
            "success": True,
            "message": "재생을 시작했습니다.",
            "now_playing": "space/Black Hole.mp4",
        }

        response = self.client.post(
            reverse("iotcore:media_server_play_video", args=[self.device.id]),
            {"video_id": "space/Black Hole.mp4"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        execute_result.assert_called_once_with(
            action="media.play_video",
            parameters={"video_id": "space/Black Hole.mp4"},
            agent_id=self.device.device_uid,
            timeout=12,
        )

    def test_play_video_rejects_empty_selection(self):
        response = self.client.post(
            reverse("iotcore:media_server_play_video", args=[self.device.id]),
            {"video_id": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_mood_endpoint_is_explicit_stub(self):
        response = self.client.post(
            reverse("iotcore:media_server_play_mood", args=[self.device.id]),
            {"mood": "우주"},
        )
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["message"], "아직 구현되지 않은 기능입니다.")

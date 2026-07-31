from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.template.loader import render_to_string
from django.urls import reverse

from .device.services.device_service import DeviceService
from .infrastructure.music_assistant.client import MusicAssistantClient


class MusicAssistantClientTests(SimpleTestCase):
    @patch.object(
        MusicAssistantClient,
        "_send_command",
        return_value=(
            True,
            [
                {"item_id": "23", "name": "광활"},
                {"item_id": "19", "name": "따뜻함"},
            ],
        ),
    )
    def test_get_playlists_requests_favorites(self, send_command):
        playlists, error = MusicAssistantClient.get_playlists()

        self.assertIsNone(error)
        self.assertEqual(
            [playlist["name"] for playlist in playlists],
            ["광활", "따뜻함"],
        )
        send_command.assert_called_once_with(
            command="music/playlists/library_items",
            args={
                "limit": 100,
                "offset": 0,
                "order_by": "sort_name",
                "favorite": True,
            },
            action_name="재생목록 조회",
            return_result=True,
        )

    @patch.object(
        MusicAssistantClient,
        "_send_command",
        return_value=(True, None),
    )
    def test_play_previous_uses_player_queue_previous_command(self, send_command):
        result = MusicAssistantClient.play_previous("player-1")

        self.assertEqual(result, (True, None))
        send_command.assert_called_once_with(
            command="player_queues/previous",
            args={"queue_id": "player-1"},
            action_name="이전 곡 재생",
        )

    @patch.object(
        MusicAssistantClient,
        "_send_command",
        return_value=(True, None),
    )
    def test_resume_uses_player_queue_resume_command(self, send_command):
        result = MusicAssistantClient.resume("player-1")

        self.assertEqual(result, (True, None))
        send_command.assert_called_once_with(
            command="player_queues/resume",
            args={"queue_id": "player-1"},
            action_name="재생 재개",
        )

    @patch.object(
        MusicAssistantClient,
        "_send_command",
        return_value=(True, None),
    )
    def test_set_repeat_uses_supported_repeat_mode(self, send_command):
        result = MusicAssistantClient.set_repeat("player-1", "all")

        self.assertEqual(result, (True, None))
        send_command.assert_called_once_with(
            command="player_queues/repeat",
            args={
                "queue_id": "player-1",
                "repeat_mode": "all",
            },
            action_name="반복 재생 설정",
        )

    def test_set_repeat_rejects_unknown_mode(self):
        success, message = MusicAssistantClient.set_repeat(
            "player-1",
            "playlist",
        )

        self.assertFalse(success)
        self.assertIn("off, all, one", message)


class DeviceServiceSpeakerTests(SimpleTestCase):
    @patch.object(
        MusicAssistantClient,
        "play_previous",
        return_value=(True, None),
    )
    @patch.object(
        MusicAssistantClient,
        "resolve_player_id",
        return_value=("player-1", None),
    )
    @patch(
        "iotcore.device.services.device_service.DeviceRepository.get_by_id",
        return_value=SimpleNamespace(
            device_uid="speaker-uid",
            name="거실 스피커",
        ),
    )
    def test_execute_speaker_delegates_previous(
        self,
        get_by_id,
        resolve_player_id,
        play_previous,
    ):
        result = DeviceService.execute_speaker(4, "play_previous")

        self.assertEqual(result, (True, None))
        get_by_id.assert_called_once_with(4)
        resolve_player_id.assert_called_once_with(
            player_id="speaker-uid",
            player_name="거실 스피커",
        )
        play_previous.assert_called_once_with("player-1")

    @patch.object(
        MusicAssistantClient,
        "resume",
        return_value=(True, None),
    )
    @patch.object(
        MusicAssistantClient,
        "resolve_player_id",
        return_value=("player-1", None),
    )
    @patch(
        "iotcore.device.services.device_service.DeviceRepository.get_by_id",
        return_value=SimpleNamespace(
            device_uid="speaker-uid",
            name="거실 스피커",
        ),
    )
    def test_execute_speaker_delegates_resume(
        self,
        get_by_id,
        resolve_player_id,
        resume,
    ):
        result = DeviceService.execute_speaker(4, "resume")

        self.assertEqual(result, (True, None))
        get_by_id.assert_called_once_with(4)
        resolve_player_id.assert_called_once_with(
            player_id="speaker-uid",
            player_name="거실 스피커",
        )
        resume.assert_called_once_with("player-1")

    @patch.object(
        MusicAssistantClient,
        "set_repeat",
        return_value=(True, None),
    )
    @patch.object(
        MusicAssistantClient,
        "resolve_player_id",
        return_value=("player-1", None),
    )
    @patch(
        "iotcore.device.services.device_service.DeviceRepository.get_by_id",
        return_value=SimpleNamespace(
            device_uid="speaker-uid",
            name="거실 스피커",
        ),
    )
    def test_execute_speaker_delegates_repeat_mode(
        self,
        get_by_id,
        resolve_player_id,
        set_repeat,
    ):
        result = DeviceService.execute_speaker(
            4,
            "set_repeat",
            repeat_mode="one",
        )

        self.assertEqual(result, (True, None))
        get_by_id.assert_called_once_with(4)
        resolve_player_id.assert_called_once_with(
            player_id="speaker-uid",
            player_name="거실 스피커",
        )
        set_repeat.assert_called_once_with("player-1", "one")


class SpeakerEndpointTests(SimpleTestCase):
    @patch(
        "iotcore.api.views.speaker.DeviceService.control",
        return_value=(True, "이전 곡을 재생합니다!"),
    )
    @patch(
        "iotcore.api.views.speaker.DeviceRepository.get_by_id",
        return_value=SimpleNamespace(id=4, device_type="speaker"),
    )
    def test_previous_endpoint_passes_motion_to_service(
        self,
        get_by_id,
        control,
    ):
        response = self.client.post(
            reverse(
                "iotcore:speaker_play_previous",
                kwargs={"device_id": 4},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        get_by_id.assert_called_once_with(4)
        self.assertEqual(control.call_args.kwargs["motion"], "play_previous")

    @patch(
        "iotcore.api.views.speaker.DeviceService.control",
        return_value=(True, "현재 곡 재생을 재개합니다!"),
    )
    @patch(
        "iotcore.api.views.speaker.DeviceRepository.get_by_id",
        return_value=SimpleNamespace(id=4, device_type="speaker"),
    )
    def test_resume_endpoint_passes_motion_to_service(
        self,
        get_by_id,
        control,
    ):
        response = self.client.post(
            reverse(
                "iotcore:speaker_resume",
                kwargs={"device_id": 4},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        get_by_id.assert_called_once_with(4)
        self.assertEqual(control.call_args.kwargs["motion"], "resume")

    @patch(
        "iotcore.api.views.speaker.DeviceService.control",
        return_value=(True, "반복 재생 모드가 설정되었습니다!"),
    )
    @patch(
        "iotcore.api.views.speaker.DeviceRepository.get_by_id",
        return_value=SimpleNamespace(id=4, device_type="speaker"),
    )
    def test_repeat_endpoint_passes_mode_to_service(
        self,
        get_by_id,
        control,
    ):
        response = self.client.post(
            reverse(
                "iotcore:speaker_set_repeat",
                kwargs={"device_id": 4, "repeat_mode": "one"},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        get_by_id.assert_called_once_with(4)
        self.assertEqual(control.call_args.kwargs["motion"], "set_repeat")
        self.assertEqual(control.call_args.kwargs["repeat_mode"], "one")

    def test_repeat_endpoint_rejects_unknown_mode(self):
        response = self.client.post(
            reverse(
                "iotcore:speaker_set_repeat",
                kwargs={"device_id": 4, "repeat_mode": "invalid"},
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])


class SpeakerTemplateTests(SimpleTestCase):
    def test_music_card_renders_transport_and_repeat_controls(self):
        html = render_to_string(
            "iotcore/detail_list.html",
            {
                "devices": [
                    SimpleNamespace(
                        id=4,
                        device_type="speaker",
                        name="JBL AUTHENTICS 300",
                    ),
                ],
                "controllers": [],
                "pcs": [],
                "playlists": [
                    {"item_id": "vast", "name": "광활"},
                    {"item_id": "drive", "name": "운전"},
                ],
                "playlists_error": None,
                "speaker_volume": 30,
            },
        )

        self.assertIn("transport-controls", html)
        self.assertIn(
            "sendSpeakerTransportAction(event, 'previous')",
            html,
        )
        self.assertIn(
            "sendSpeakerTransportAction(event, 'resume')",
            html,
        )
        self.assertIn("이전 곡", html)
        self.assertIn("현재 곡 재생", html)
        self.assertIn("repeat-controls", html)
        self.assertIn("sendSpeakerRepeat(event, 'off')", html)
        self.assertIn("sendSpeakerRepeat(event, 'all')", html)
        self.assertIn("sendSpeakerRepeat(event, 'one')", html)
        self.assertIn("반복 재생 없음", html)
        self.assertIn("전체 반복 재생", html)
        self.assertIn("현재 곡 반복", html)

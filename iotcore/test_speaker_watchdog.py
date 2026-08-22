from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from .infrastructure.music_assistant.client import MusicAssistantClient
from .infrastructure.music_assistant.watchdog import SpeakerPlaybackWatchdog


def player(state="playing", elapsed=4):
    return {
        "state": state,
        "available": True,
        "enabled": True,
        "powered": True,
        "volume_muted": False,
        "elapsed_time": elapsed,
        "active_output_protocol": "cast-1",
    }


def queue(state="playing", elapsed=4, active=True):
    return {
        "state": state,
        "available": True,
        "active": active,
        "current_index": 5,
        "items": 59,
        "elapsed_time": elapsed,
        "current_item": {"media_item": {"name": "테스트 곡"}},
    }


class MusicAssistantStateQueryTests(SimpleTestCase):
    def test_get_player_state_uses_players_get(self):
        with self.settings(
            MUSIC_ASSISTANT_URL="http://music-assistant",
            MUSIC_ASSISTANT_TOKEN="test-token",
        ):
            with self.subTest("request shape"):
                with MockSendCommand({"state": "playing"}) as send_command:
                    result, error = MusicAssistantClient.get_player_state("player-1")

        self.assertIsNone(error)
        self.assertEqual(result["state"], "playing")
        send_command.assert_called_once_with(
            command="players/get",
            args={"player_id": "player-1"},
            action_name="플레이어 상태 조회",
            return_result=True,
        )

    def test_get_queue_state_uses_player_queue_get(self):
        with MockSendCommand({"state": "idle"}) as send_command:
            result, error = MusicAssistantClient.get_queue_state("player-1")

        self.assertIsNone(error)
        self.assertEqual(result["state"], "idle")
        send_command.assert_called_once_with(
            command="player_queues/get",
            args={"queue_id": "player-1"},
            action_name="플레이어 큐 상태 조회",
            return_result=True,
        )


class MockSendCommand:
    def __init__(self, result):
        self.mock = Mock(return_value=(True, result))
        self.patcher = None

    def __enter__(self):
        from unittest.mock import patch

        self.patcher = patch.object(
            MusicAssistantClient,
            "_send_command",
            self.mock,
        )
        self.patcher.start()
        return self.mock

    def __exit__(self, exc_type, exc_value, traceback):
        self.patcher.stop()


class SpeakerPlaybackWatchdogTests(SimpleTestCase):
    def setUp(self):
        self.client = Mock()
        self.client.resume.return_value = (True, None)
        self.watchdog = SpeakerPlaybackWatchdog(
            client=self.client,
            idle_grace=6,
            max_early_elapsed=20,
            recent_playing_window=20,
            rearm_after=60,
        )

    def test_does_not_start_an_idle_player_on_watchdog_start(self):
        recovered = self.watchdog.observe(
            "player-1",
            player("idle"),
            queue("idle"),
            now=100,
        )

        self.assertFalse(recovered)
        self.client.resume.assert_not_called()

    def test_resumes_once_after_confirmed_early_idle(self):
        self.watchdog.observe(
            "player-1",
            player(),
            queue(),
            now=100,
        )
        self.watchdog.observe(
            "player-1",
            player("idle"),
            queue("idle"),
            now=102,
        )
        recovered = self.watchdog.observe(
            "player-1",
            player("idle"),
            queue("idle"),
            now=108,
        )
        repeated = self.watchdog.observe(
            "player-1",
            player("idle"),
            queue("idle"),
            now=110,
        )

        self.assertTrue(recovered)
        self.assertFalse(repeated)
        self.client.resume.assert_called_once_with("player-1")

    def test_does_not_resume_pause_power_off_or_inactive_queue(self):
        cases = (
            (player("paused"), queue("paused")),
            ({**player("idle"), "powered": False}, queue("idle")),
            (player("idle"), queue("idle", active=False)),
        )
        for index, (player_state, queue_state) in enumerate(cases):
            player_id = f"player-{index}"
            self.watchdog.observe(
                player_id,
                player(),
                queue(),
                now=100,
            )
            self.watchdog.observe(
                player_id,
                player_state,
                queue_state,
                now=102,
            )
            self.watchdog.observe(
                player_id,
                player_state,
                queue_state,
                now=110,
            )

        self.client.resume.assert_not_called()

    def test_does_not_resume_a_late_track_idle(self):
        self.watchdog.observe(
            "player-1",
            player(elapsed=120),
            queue(elapsed=120),
            now=100,
        )
        self.watchdog.observe(
            "player-1",
            player("idle", elapsed=120),
            queue("idle", elapsed=120),
            now=102,
        )
        self.watchdog.observe(
            "player-1",
            player("idle", elapsed=120),
            queue("idle", elapsed=120),
            now=110,
        )

        self.client.resume.assert_not_called()

    def test_rearms_only_after_stable_playback(self):
        self.watchdog.observe("player-1", player(), queue(), now=100)
        self.watchdog.observe(
            "player-1", player("idle"), queue("idle"), now=102
        )
        self.watchdog.observe(
            "player-1", player("idle"), queue("idle"), now=108
        )
        self.watchdog.observe("player-1", player(), queue(), now=110)
        self.watchdog.observe("player-1", player(), queue(), now=170)
        self.watchdog.observe(
            "player-1", player("idle"), queue("idle"), now=172
        )
        recovered = self.watchdog.observe(
            "player-1", player("idle"), queue("idle"), now=178
        )

        self.assertTrue(recovered)
        self.assertEqual(self.client.resume.call_count, 2)


class SpeakerWatchdogCommandTests(SimpleTestCase):
    @patch(
        "iotcore.management.commands.run_speaker_watchdog."
        "MusicAssistantClient.resolve_player_id",
        return_value=("up-player-1", None),
    )
    @patch(
        "iotcore.management.commands.run_speaker_watchdog."
        "SpeakerPlaybackWatchdog"
    )
    @patch(
        "iotcore.management.commands.run_speaker_watchdog.Device.objects.filter"
    )
    def test_resolves_database_alias_before_checking_player(
        self,
        filter_devices,
        watchdog_class,
        resolve_player_id,
    ):
        filter_devices.return_value.only.return_value = [
            SimpleNamespace(
                pk=4,
                device_uid="jbl",
                name="JBL AUTHENTICS 300",
            )
        ]

        call_command(
            "run_speaker_watchdog",
            "--once",
            stdout=StringIO(),
            stderr=StringIO(),
        )

        resolve_player_id.assert_called_once_with(
            player_id="jbl",
            player_name="JBL AUTHENTICS 300",
        )
        watchdog_class.return_value.check.assert_called_once_with(
            "up-player-1",
            player_name="JBL AUTHENTICS 300",
        )

    @patch(
        "iotcore.management.commands.run_scheduler."
        "AutomationService.process_weather_conditions",
        return_value=[],
    )
    @patch(
        "iotcore.management.commands.run_scheduler.AutomationService.enqueue_due",
        return_value=[],
    )
    @patch(
        "iotcore.management.commands.run_scheduler."
        "MusicAssistantClient.resolve_player_id",
        return_value=("up-player-1", None),
    )
    @patch(
        "iotcore.management.commands.run_scheduler.SpeakerPlaybackWatchdog"
    )
    @patch("iotcore.management.commands.run_scheduler.Device.objects.filter")
    def test_scheduler_checks_resolved_speaker_without_blocking_automations(
        self,
        filter_devices,
        watchdog_class,
        resolve_player_id,
        enqueue_due,
        process_weather_conditions,
    ):
        filter_devices.return_value.only.return_value = [
            SimpleNamespace(
                pk=4,
                device_uid="jbl",
                name="JBL AUTHENTICS 300",
            )
        ]

        call_command(
            "run_scheduler",
            "--once",
            stdout=StringIO(),
            stderr=StringIO(),
        )

        enqueue_due.assert_called_once_with()
        process_weather_conditions.assert_called_once_with()
        resolve_player_id.assert_called_once_with(
            player_id="jbl",
            player_name="JBL AUTHENTICS 300",
        )
        watchdog_class.return_value.check.assert_called_once_with(
            "up-player-1",
            player_name="JBL AUTHENTICS 300",
        )

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ...infrastructure.music_assistant.client import MusicAssistantClient
from ...infrastructure.music_assistant.watchdog import SpeakerPlaybackWatchdog
from ...models import Device
from ...scheduler.service import AutomationService


class Command(BaseCommand):
    help = "Enqueue due time-based IoTCore automation triggers."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=5)

    def handle(self, *args, **options):
        logging.getLogger(
            "iotcore.infrastructure.music_assistant.watchdog"
        ).setLevel(logging.INFO)
        speaker_watchdog = SpeakerPlaybackWatchdog(
            idle_grace=6,
            max_early_elapsed=20,
            recent_playing_window=20,
            rearm_after=60,
        )
        resolved_players = {}

        while True:
            try:
                close_old_connections()
                runs = AutomationService.enqueue_due()
                if runs:
                    self.stdout.write(f"{len(runs)}개 실행 요청을 등록했습니다.")
            except Exception as exc:
                self.stderr.write(
                    f"시간 예약 실행 처리 실패: {type(exc).__name__}: {exc}"
                )
                if options["once"]:
                    raise
            try:
                self._check_speakers(
                    speaker_watchdog,
                    resolved_players,
                )
            except Exception as exc:
                self.stderr.write(
                    "스피커 재생 감시 실패: "
                    f"{type(exc).__name__}: {exc}"
                )
                if options["once"]:
                    raise
            finally:
                close_old_connections()

            if options["once"]:
                return
            time.sleep(max(options["poll_interval"], 0.2))

    @staticmethod
    def _check_speakers(watchdog, resolved_players):
        speakers = Device.objects.filter(
            device_type="speaker",
            protocol=Device.Protocol.TCPIP,
        ).only("device_uid", "name")
        for speaker in speakers:
            lookup_key = (
                speaker.pk,
                speaker.device_uid,
                speaker.name,
            )
            player_id = resolved_players.get(lookup_key)
            if not player_id:
                player_id, error = MusicAssistantClient.resolve_player_id(
                    player_id=speaker.device_uid,
                    player_name=speaker.name,
                )
                if not player_id:
                    raise RuntimeError(
                        f"{speaker.name} 플레이어 확인 실패: {error}"
                    )
                resolved_players[lookup_key] = player_id
            watchdog.check(
                player_id,
                player_name=speaker.name,
            )

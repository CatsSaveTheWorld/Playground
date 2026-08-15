import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ...infrastructure.music_assistant.client import MusicAssistantClient
from ...infrastructure.music_assistant.watchdog import SpeakerPlaybackWatchdog
from ...models import Device


class Command(BaseCommand):
    help = "Recover brief Music Assistant speaker playback dropouts."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=2.0)
        parser.add_argument("--idle-grace", type=float, default=6.0)
        parser.add_argument("--max-early-elapsed", type=float, default=20.0)
        parser.add_argument("--recent-playing-window", type=float, default=20.0)
        parser.add_argument("--rearm-after", type=float, default=60.0)

    def handle(self, *args, **options):
        logging.getLogger(
            "iotcore.infrastructure.music_assistant.watchdog"
        ).setLevel(logging.INFO)
        watchdog = SpeakerPlaybackWatchdog(
            idle_grace=options["idle_grace"],
            max_early_elapsed=options["max_early_elapsed"],
            recent_playing_window=options["recent_playing_window"],
            rearm_after=options["rearm_after"],
        )
        self.stdout.write(
            "스피커 재생 감시를 시작합니다. "
            f"(poll={max(options['poll_interval'], 0.5):g}s, "
            f"grace={max(options['idle_grace'], 0):g}s)"
        )
        resolved_players = {}

        while True:
            try:
                close_old_connections()
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
                            self.stderr.write(
                                "스피커 플레이어 확인 실패: "
                                f"{speaker.name}: {error}"
                            )
                            continue
                        resolved_players[lookup_key] = player_id
                    watchdog.check(
                        player_id,
                        player_name=speaker.name,
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
            time.sleep(max(options["poll_interval"], 0.5))

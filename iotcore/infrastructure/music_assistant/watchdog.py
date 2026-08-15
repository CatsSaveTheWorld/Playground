"""Recover short-lived Music Assistant speaker playback dropouts safely."""

from dataclasses import dataclass
import json
import logging
import time

from .client import MusicAssistantClient


LOGGER = logging.getLogger(__name__)


@dataclass
class _PlayerWatchState:
    was_playing: bool = False
    last_playing_at: float | None = None
    stable_playing_since: float | None = None
    suspicious_since: float | None = None
    recovery_attempted: bool = False
    last_signature: tuple | None = None


class SpeakerPlaybackWatchdog:
    """Watch a player queue and resume only a narrowly defined Cast dropout."""

    def __init__(
        self,
        client=MusicAssistantClient,
        idle_grace=6.0,
        max_early_elapsed=20.0,
        recent_playing_window=20.0,
        rearm_after=60.0,
        clock=None,
    ):
        self.client = client
        self.idle_grace = max(float(idle_grace), 0.0)
        self.max_early_elapsed = max(float(max_early_elapsed), 0.0)
        self.recent_playing_window = max(float(recent_playing_window), 0.0)
        self.rearm_after = max(float(rearm_after), 0.0)
        self.clock = clock or time.monotonic
        self._states = {}

    def check(self, player_id, player_name=None):
        """Query one player and recover it when a transient idle is confirmed."""
        player, error = self.client.get_player_state(player_id)
        if error:
            LOGGER.warning(
                "speaker-watchdog player query failed player=%s name=%s error=%s",
                player_id,
                player_name or "",
                error,
            )
            return False

        queue, error = self.client.get_queue_state(player_id)
        if error:
            LOGGER.warning(
                "speaker-watchdog queue query failed player=%s name=%s error=%s",
                player_id,
                player_name or "",
                error,
            )
            return False

        return self.observe(
            player_id,
            player,
            queue,
            player_name=player_name,
        )

    def observe(self, player_id, player, queue, player_name=None, now=None):
        """Process one state sample; return True only when resume was sent."""
        now = self.clock() if now is None else float(now)
        state = self._states.setdefault(player_id, _PlayerWatchState())

        player_state = str(player.get("state") or "unknown").lower()
        queue_state = str(queue.get("state") or "unknown").lower()
        signature = (
            player_state,
            queue_state,
            bool(player.get("available")),
            bool(player.get("powered")),
            bool(queue.get("active")),
            queue.get("current_index"),
            self._current_item_name(queue),
        )
        if signature != state.last_signature:
            LOGGER.info(
                "speaker-watchdog state player=%s name=%s snapshot=%s",
                player_id,
                player_name or "",
                json.dumps(
                    self._snapshot(player, queue),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            state.last_signature = signature

        if player_state == "playing" and queue_state == "playing":
            state.was_playing = True
            state.last_playing_at = now
            state.suspicious_since = None
            if state.stable_playing_since is None:
                state.stable_playing_since = now
            if (
                state.recovery_attempted
                and now - state.stable_playing_since >= self.rearm_after
            ):
                state.recovery_attempted = False
                LOGGER.info(
                    "speaker-watchdog rearmed player=%s name=%s stable_seconds=%.1f",
                    player_id,
                    player_name or "",
                    now - state.stable_playing_since,
                )
            return False

        state.stable_playing_since = None
        if not self._is_recoverable_idle(state, player, queue, now):
            state.suspicious_since = None
            return False

        if state.suspicious_since is None:
            state.suspicious_since = now
            LOGGER.warning(
                "speaker-watchdog suspicious idle player=%s name=%s snapshot=%s",
                player_id,
                player_name or "",
                json.dumps(
                    self._snapshot(player, queue),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            return False

        idle_for = now - state.suspicious_since
        if idle_for < self.idle_grace or state.recovery_attempted:
            return False

        state.recovery_attempted = True
        success, error = self.client.resume(player_id)
        if success:
            LOGGER.warning(
                "speaker-watchdog resumed player=%s name=%s idle_seconds=%.1f",
                player_id,
                player_name or "",
                idle_for,
            )
            return True

        LOGGER.error(
            "speaker-watchdog resume failed player=%s name=%s error=%s",
            player_id,
            player_name or "",
            error,
        )
        return False

    def _is_recoverable_idle(self, state, player, queue, now):
        if not state.was_playing or state.last_playing_at is None:
            return False
        if now - state.last_playing_at > self.recent_playing_window:
            return False
        if state.recovery_attempted:
            return False
        if str(player.get("state") or "").lower() != "idle":
            return False
        if str(queue.get("state") or "").lower() != "idle":
            return False
        if not all(
            (
                player.get("available") is True,
                player.get("enabled") is True,
                player.get("powered") is True,
                player.get("volume_muted") is not True,
                queue.get("available") is True,
                queue.get("active") is True,
                bool(queue.get("current_item")),
                self._queue_item_count(queue) > 0,
            )
        ):
            return False

        elapsed = self._elapsed_time(player, queue)
        return elapsed is not None and elapsed <= self.max_early_elapsed

    @staticmethod
    def _elapsed_time(player, queue):
        value = queue.get("elapsed_time")
        if value is None:
            value = player.get("elapsed_time")
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return max(value, 0.0)

    @staticmethod
    def _queue_item_count(queue):
        items = queue.get("items")
        if isinstance(items, bool):
            return 0
        if isinstance(items, (list, tuple)):
            return len(items)
        try:
            return max(int(items), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _current_item_name(queue):
        item = queue.get("current_item")
        if not isinstance(item, dict):
            return ""
        media_item = item.get("media_item")
        if isinstance(media_item, dict) and media_item.get("name"):
            return str(media_item["name"])
        return str(item.get("name") or "")

    @classmethod
    def _snapshot(cls, player, queue):
        return {
            "active_output_protocol": player.get("active_output_protocol"),
            "current_index": queue.get("current_index"),
            "current_item": cls._current_item_name(queue),
            "elapsed_time": cls._elapsed_time(player, queue),
            "player_available": player.get("available"),
            "player_enabled": player.get("enabled"),
            "player_powered": player.get("powered"),
            "player_state": player.get("state"),
            "queue_active": queue.get("active"),
            "queue_available": queue.get("available"),
            "queue_items": cls._queue_item_count(queue),
            "queue_state": queue.get("state"),
            "repeat_mode": queue.get("repeat_mode"),
            "shuffle_enabled": queue.get("shuffle_enabled"),
            "volume_muted": player.get("volume_muted"),
        }

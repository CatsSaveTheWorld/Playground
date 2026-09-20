"""Zigbee sliding-window pusher control and state helpers.

The ZC-LP01 exposes a cover-style Zigbee2MQTT interface.  This module keeps
physical-direction mapping, state derivation and the 15-second fail-safe stop in
one place so the web UI, Automation worker and AI entry point all behave the
same way.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from django.db import transaction
from django.utils import timezone

from ...infrastructure.zigbee.client import ZigbeeClient
from ...models import Action, ActionRun, AutomationRun, Device, DeviceState
from .device_state_service import DeviceStateService


logger = logging.getLogger(__name__)


class WindowPusherService:
    DEVICE_TYPE = "window_pusher"
    DEFAULT_SAFETY_STOP_SECONDS = 15
    MIN_SAFETY_STOP_SECONDS = 1
    MAX_SAFETY_STOP_SECONDS = 60
    DEFAULT_POSITION_TOLERANCE = 5
    SAFETY_RUN_PREFIX = "__window_pusher_safety_stop__"

    @classmethod
    def _config(cls, device: Device) -> dict:
        return dict(device.control_config or {})

    @classmethod
    def safety_stop_seconds(cls, device: Device) -> int:
        raw = cls._config(device).get(
            "safety_stop_seconds",
            cls.DEFAULT_SAFETY_STOP_SECONDS,
        )
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            seconds = cls.DEFAULT_SAFETY_STOP_SECONDS
        return max(
            cls.MIN_SAFETY_STOP_SECONDS,
            min(cls.MAX_SAFETY_STOP_SECONDS, seconds),
        )

    @classmethod
    def _direction_command(cls, device: Device, direction: str) -> str:
        config = cls._config(device)
        default = "OPEN" if direction == "left" else "CLOSE"
        command = str(config.get(f"{direction}_command") or default).upper()
        if command not in {"OPEN", "CLOSE"}:
            command = default
        return command

    @classmethod
    def closed_position(cls, device: Device) -> int:
        raw = cls._config(device).get("closed_position", 0)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        return max(0, min(100, value))

    @classmethod
    def open_position(cls, device: Device) -> int:
        config = cls._config(device)
        raw = config.get("open_position")
        if raw in (None, ""):
            return 100 if cls.closed_position(device) <= 50 else 0
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 100 if cls.closed_position(device) <= 50 else 0
        return max(0, min(100, value))

    @classmethod
    def position_tolerance(cls, device: Device) -> int:
        raw = cls._config(device).get(
            "position_tolerance",
            cls.DEFAULT_POSITION_TOLERANCE,
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = cls.DEFAULT_POSITION_TOLERANCE
        return max(0, min(20, value))

    @staticmethod
    def parse_position(value) -> int:
        if isinstance(value, bool):
            raise ValueError("창문 위치는 0부터 100 사이의 정수로 입력하세요.")
        if isinstance(value, int):
            position = value
        elif isinstance(value, float) and value.is_integer():
            position = int(value)
        elif isinstance(value, str) and value.strip().isdigit():
            position = int(value.strip())
        else:
            raise ValueError("창문 위치는 0부터 100 사이의 정수로 입력하세요.")
        if not 0 <= position <= 100:
            raise ValueError("창문 위치는 0부터 100 사이여야 합니다.")
        return position

    @classmethod
    def state_from_position(cls, device: Device, value) -> str | None:
        try:
            position = cls.parse_position(value)
        except ValueError:
            return None

        tolerance = cls.position_tolerance(device)
        if abs(position - cls.closed_position(device)) <= tolerance:
            return "closed"
        if abs(position - cls.open_position(device)) <= tolerance:
            return "open"
        return "partial"

    @classmethod
    def normalize_report(cls, device: Device, payload: dict) -> dict:
        """Add canonical window-state fields to a Zigbee2MQTT report."""
        normalized = dict(payload or {})

        if "position" in normalized:
            try:
                position = cls.parse_position(normalized["position"])
            except ValueError:
                position = None
            if position is not None:
                normalized["position"] = position
                window_state = cls.state_from_position(device, position)
                if window_state is not None:
                    normalized["window_state"] = window_state
                if window_state in {"open", "closed"}:
                    normalized["motion_direction"] = None
                    normalized["target_position"] = None
                else:
                    target = DeviceStateService.get_value(
                        device,
                        "target_position",
                    )
                    try:
                        target_position = cls.parse_position(target)
                    except ValueError:
                        target_position = None
                    if (
                        target_position is not None
                        and abs(position - target_position) <= cls.position_tolerance(device)
                    ):
                        normalized["motion_direction"] = None
                        normalized["target_position"] = None

        raw_state = normalized.get("state")
        if isinstance(raw_state, str) and raw_state.upper() == "STOP":
            normalized["motion_direction"] = None
            normalized["target_position"] = None

        return normalized

    @classmethod
    def optimistic_state_patch(
        cls,
        action: str,
        *,
        position: int | None = None,
    ) -> dict:
        if action == "move_left":
            return {
                "motion_direction": "left",
                "target_position": None,
                "last_command": "move_left",
            }
        if action == "move_right":
            return {
                "motion_direction": "right",
                "target_position": None,
                "last_command": "move_right",
            }
        if action in {"stop", "safety_stop"}:
            return {
                "motion_direction": None,
                "target_position": None,
                "last_command": "stop",
            }
        if action == "set_position":
            return {
                "motion_direction": "position",
                "target_position": position,
                "last_command": "set_position",
            }
        return {}

    @classmethod
    def execute(
        cls,
        device: Device,
        action: str,
        *,
        position=None,
    ) -> tuple[bool, str]:
        if action == "move_left":
            payload = {"state": cls._direction_command(device, "left")}
            success_message = "창문을 왼쪽으로 이동합니다."
        elif action == "move_right":
            payload = {"state": cls._direction_command(device, "right")}
            success_message = "창문을 오른쪽으로 이동합니다."
        elif action in {"stop", "safety_stop"}:
            payload = {"state": "STOP"}
            success_message = (
                "창문 푸셔 안전 정지 명령을 전송했습니다."
                if action == "safety_stop"
                else "창문 이동을 정지했습니다."
            )
        elif action == "set_position":
            try:
                position = cls.parse_position(position)
            except ValueError as exc:
                return False, str(exc)
            payload = {"position": position}
            success_message = f"창문 위치를 {position}%로 이동합니다."
        else:
            return False, f"지원하지 않는 창문 푸셔 동작입니다. ({action})"

        success, error = ZigbeeClient.send_payload(device.device_uid, payload)
        if not success:
            return False, error or "창문 푸셔 MQTT 명령 전송에 실패했습니다."

        if action == "stop":
            cls.cancel_pending_safety_stops(device)
        elif action != "safety_stop":
            try:
                cls.replace_safety_stop(device)
            except Exception:
                # The motor command has already left the broker.  If the
                # durable queue cannot be written, fail closed by sending STOP
                # immediately instead of leaving a motor without its deadline.
                logger.exception(
                    "Failed to schedule window-pusher safety STOP for %s",
                    device.device_uid,
                )
                ZigbeeClient.send_payload(device.device_uid, {"state": "STOP"})
                return False, (
                    "창문 이동 명령은 전송됐지만 안전 정지 예약에 실패해 "
                    "즉시 STOP 명령을 전송했습니다."
                )

        return True, success_message

    @classmethod
    def _safety_run_name(cls, device: Device) -> str:
        return f"{cls.SAFETY_RUN_PREFIX}:{device.pk}"

    @classmethod
    def cancel_pending_safety_stops(cls, device: Device) -> int:
        """Cancel only fail-safe STOPs that have not started yet.

        A STOP already claimed by the worker must be allowed to finish.  This
        also avoids marking a safety run cancelled after its STOP was sent.
        """
        now = timezone.now()
        pending_actions = ActionRun.objects.filter(
            root_run__automation__isnull=True,
            root_run__automation_name=cls._safety_run_name(device),
            device=device,
            function="safety_stop",
            status__in=[
                AutomationRun.Status.PENDING,
                AutomationRun.Status.WAITING,
            ],
        )
        run_ids = list(pending_actions.values_list("root_run_id", flat=True))
        if not run_ids:
            return 0

        with transaction.atomic():
            cancelled_actions = pending_actions.update(
                status=AutomationRun.Status.CANCELLED,
                message="새 창문 명령으로 기존 안전 정지를 교체했습니다.",
                finished_at=now,
            )
            AutomationRun.objects.filter(
                id__in=run_ids,
                status__in=[
                    AutomationRun.Status.PENDING,
                    AutomationRun.Status.WAITING,
                    AutomationRun.Status.RUNNING,
                ],
            ).update(
                status=AutomationRun.Status.CANCELLED,
                message="새 창문 명령으로 기존 안전 정지를 교체했습니다.",
                finished_at=now,
            )
        return cancelled_actions

    @classmethod
    def replace_safety_stop(cls, device: Device) -> ActionRun:
        """Persist a STOP command that the automation worker can execute later.

        No sleep/timer thread is used.  The action is durable in the same queue
        consumed by ``iotcore-automation-worker`` and therefore survives an
        Apache worker recycle.
        """
        seconds = cls.safety_stop_seconds(device)
        now = timezone.now()
        due_at = now + timedelta(seconds=seconds)

        with transaction.atomic():
            cls.cancel_pending_safety_stops(device)
            run = AutomationRun.objects.create(
                automation=None,
                automation_name=cls._safety_run_name(device),
                source=AutomationRun.Source.API,
                status=AutomationRun.Status.RUNNING,
                planned_at=now,
                started_at=now,
                trigger_payload={
                    "type": "window_pusher_safety_stop",
                    "device_id": device.pk,
                    "device_uid": device.device_uid,
                    "delay_seconds": seconds,
                },
                message=f"{seconds}초 후 창문 푸셔 안전 정지 예정",
            )
            return ActionRun.objects.create(
                automation_run=run,
                root_run=run,
                action=None,
                device=device,
                target_automation=None,
                step_order=1,
                order=1,
                action_type=Action.Type.DEVICE,
                function="safety_stop",
                parameter={"safety_stop": True},
                delay=seconds,
                delay_position=Action.DelayPosition.BEFORE,
                delay_applied=True,
                available_at=due_at,
                status=AutomationRun.Status.WAITING,
                message=f"{seconds}초 안전 정지 대기 중",
            )

    @classmethod
    def snapshot(cls, device: Device) -> dict:
        """Return UI-friendly canonical state plus per-key update timestamps."""
        topic = DeviceStateService.canonical_topic(device)
        rows = list(DeviceState.objects.filter(topic=topic))
        values = {row.key: row.value for row in rows}
        updated_at = {
            row.key: row.updated_at.isoformat() if row.updated_at else None
            for row in rows
        }

        position = values.get("position")
        if values.get("window_state") in (None, "unknown") and position is not None:
            values["window_state"] = cls.state_from_position(device, position)

        return {
            "device_id": device.pk,
            "device_uid": device.device_uid,
            "online": values.get("online"),
            "battery": values.get("battery"),
            "charging": values.get("charging"),
            "position": values.get("position"),
            "window_state": values.get("window_state"),
            "motion_direction": values.get("motion_direction"),
            "target_position": values.get("target_position"),
            "raw_state": values.get("state"),
            "linkquality": values.get("linkquality"),
            "automatic_mode": values.get("automatic_mode"),
            "slow_stop": values.get("slow_stop"),
            "button_position": values.get("button_position"),
            "last_command": values.get("last_command"),
            "updated_at": updated_at,
            "safety_stop_seconds": cls.safety_stop_seconds(device),
        }

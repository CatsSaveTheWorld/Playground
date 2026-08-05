import time
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from ..device.services.device_service import DeviceService
from ..device.services.sequence_executor import SequenceExecutor
from ..models import (
    AutomationAction,
    AutomationActionRun,
    AutomationRun,
    SequenceRun,
)


class AutomationExecutor:
    @classmethod
    def run_next_pending(cls):
        with transaction.atomic():
            automation_run = (
                AutomationRun.objects
                .select_for_update(skip_locked=True)
                .select_related("automation")
                .filter(status=AutomationRun.Status.PENDING)
                .order_by("created_at")
                .first()
            )
            if automation_run is None:
                return None
            automation_run.status = AutomationRun.Status.RUNNING
            automation_run.started_at = timezone.now()
            automation_run.save(update_fields=["status", "started_at"])

        cls.execute_run(automation_run)
        return automation_run

    @classmethod
    def execute_run(cls, automation_run):
        automation = automation_run.automation
        if automation is None:
            return cls._finish(automation_run, False, "삭제된 자동화입니다.")

        actions = list(
            automation.actions
            .select_related("device", "sequence")
            .order_by("order", "id")
        )
        if not actions:
            return cls._finish(automation_run, False, "등록된 실행 동작이 없습니다.")

        try:
            for action in actions:
                if action.delay:
                    time.sleep(action.delay)

                action_run = AutomationActionRun.objects.create(
                    automation_run=automation_run,
                    automation_action=action,
                    order=action.order,
                    status=AutomationRun.Status.RUNNING,
                    started_at=timezone.now(),
                )

                if action.action_type == AutomationAction.ActionType.SEQUENCE:
                    sequence_run = SequenceRun.objects.create(
                        sequence=action.sequence,
                        automation=automation,
                        trigger=SequenceRun.Trigger.AUTOMATION,
                        trigger_payload=automation_run.trigger_payload,
                    )
                    action_run.sequence_run = sequence_run
                    success, message = SequenceExecutor.execute_run(sequence_run)
                else:
                    step_like = SimpleNamespace(
                        device=action.device,
                        function=action.function,
                        parameter=action.parameter,
                    )
                    try:
                        success, message = DeviceService.execute_step(step_like)
                    except Exception as exc:
                        success = False
                        message = f"개별 기기 동작 중 예외가 발생했습니다. ({exc})"

                action_run.status = (
                    AutomationRun.Status.SUCCESS
                    if success else AutomationRun.Status.FAILED
                )
                action_run.message = message or ""
                action_run.finished_at = timezone.now()
                action_run.save(update_fields=[
                    "status", "message", "finished_at", "sequence_run"
                ])

                if not success:
                    return cls._finish(automation_run, False, message)
        except Exception as exc:
            return cls._finish(
                automation_run,
                False,
                f"자동화 실행 중 예외가 발생했습니다. ({exc})",
            )

        return cls._finish(automation_run, True, "자동화 실행 완료")

    @staticmethod
    def _finish(automation_run, success, message):
        automation_run.status = (
            AutomationRun.Status.SUCCESS if success else AutomationRun.Status.FAILED
        )
        automation_run.message = message or ""
        automation_run.finished_at = timezone.now()
        automation_run.save(update_fields=["status", "message", "finished_at"])
        return success, message

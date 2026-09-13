from django.db import transaction
from django.utils import timezone

from ..models import ActionRun, AutomationRun


class DeviceControlSupersessionService:
    """Cancel delayed controls that were superseded by a newer device command.

    The unit of cancellation is the Device, not the whole AutomationRun.  A
    manual command therefore cancels only pending ActionRuns for that Device;
    pending actions for other Devices in the same AutomationRun remain valid.

    Automation actions exclude their own parent run so an intentional sequence
    such as ``fan -> 1 hour -> power_off`` does not cancel its own future action.
    """

    SUPERSEDED_MESSAGE = "더 새로운 기기 제어로 대체되어 취소됨"

    @classmethod
    def cancel_pending_for_device(
        cls,
        device,
        *,
        exclude_automation_run_id=None,
        message=None,
    ):
        if device is None:
            return []

        now = timezone.now()
        message = message or cls.SUPERSEDED_MESSAGE
        cancelled_ids = []
        affected_run_ids = set()

        # Lock the rows we are about to cancel.  A worker that already claimed
        # an ActionRun has changed it to RUNNING and is intentionally left alone;
        # this service only replaces controls that are still waiting.
        with transaction.atomic():
            queryset = (
                ActionRun.objects
                .select_for_update()
                .filter(
                    status=AutomationRun.Status.PENDING,
                    action__device=device,
                )
            )
            if exclude_automation_run_id is not None:
                queryset = queryset.exclude(
                    automation_run_id=exclude_automation_run_id,
                )

            rows = list(queryset.order_by("scheduled_for", "id"))
            for action_run in rows:
                action_run.status = AutomationRun.Status.CANCELLED
                action_run.message = message
                action_run.finished_at = now
                action_run.save(
                    update_fields=["status", "message", "finished_at"]
                )
                cancelled_ids.append(action_run.pk)
                affected_run_ids.add(action_run.automation_run_id)

        for run_id in affected_run_ids:
            cls._finalize_if_no_active_actions(run_id)

        return cancelled_ids

    @classmethod
    def _finalize_if_no_active_actions(cls, run_id):
        """Finish a parent run when device-level cancellation emptied it.

        If other Devices still have pending/running actions, the parent remains
        RUNNING.  This is what makes cancellation truly device-scoped.
        """
        run = AutomationRun.objects.filter(pk=run_id).first()
        if run is None or run.status != AutomationRun.Status.RUNNING:
            return

        active = run.action_runs.filter(
            status__in=[
                AutomationRun.Status.PENDING,
                AutomationRun.Status.RUNNING,
            ]
        ).exists()
        if active:
            return

        statuses = list(run.action_runs.values_list("status", flat=True))
        if not statuses:
            return

        now = timezone.now()
        if AutomationRun.Status.FAILED in statuses:
            run.status = AutomationRun.Status.FAILED
            run.message = "자동화 동작 중 실패가 발생했습니다."
        elif all(status in {
            AutomationRun.Status.CANCELLED,
            AutomationRun.Status.SKIPPED,
        } for status in statuses):
            run.status = AutomationRun.Status.CANCELLED
            run.message = "대기 중인 기기 동작이 더 새로운 제어로 대체되었습니다."
        else:
            cancelled_count = statuses.count(AutomationRun.Status.CANCELLED)
            run.status = AutomationRun.Status.SUCCESS
            run.message = (
                f"자동화 실행 완료 ({cancelled_count}개 기기 동작 대체됨)"
                if cancelled_count
                else "자동화 실행 완료"
            )
        run.finished_at = now
        run.save(update_fields=["status", "message", "finished_at"])

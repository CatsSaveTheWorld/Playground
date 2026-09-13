import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..device.services.device_service import DeviceService
from ..models import Action, ActionRun, AutomationRun
from .service import AutomationService
from .supersession import DeviceControlSupersessionService


class AutomationExecutor:
    """Single execution engine for immediate and scheduled Automations.

    User-configured delays are represented by ``ActionRun.scheduled_for`` and
    never block the worker.  Only the short 0.5 second compatibility delay used
    by the synchronous ``execute()`` path remains a real sleep.
    """

    DEFAULT_ACTION_DELAY = 0.5

    @classmethod
    def enqueue(cls, automation, *, source=AutomationRun.Source.MANUAL, step=None, trigger_payload=None):
        return AutomationRun.objects.create(
            automation=automation,
            automation_name=automation.name,
            step=step,
            source=source,
            trigger_payload=trigger_payload or {},
            status=AutomationRun.Status.PENDING,
        )

    @classmethod
    def execute(cls, automation, *, source=AutomationRun.Source.MANUAL, trigger_payload=None):
        """Compatibility synchronous execution path.

        Web/manual runtime execution uses ``enqueue()`` + the systemd worker.
        This method is kept for internal/tests/legacy callers that expect an
        immediate ``(success, message)`` result.
        """
        run = cls.enqueue(
            automation,
            source=source,
            trigger_payload=trigger_payload,
        )
        return cls.execute_run(run)

    # ------------------------------------------------------------------
    # Non-blocking worker path
    # ------------------------------------------------------------------
    @classmethod
    def run_next_pending(cls):
        """Process one ready unit of work without sleeping for user delays.

        Priority is given to an ActionRun whose scheduled time has arrived.  If
        none is ready, a new AutomationRun is planned into pending ActionRuns.
        After planning, its first immediately-due action is executed in the same
        call for low latency and backwards-compatible worker behaviour.
        """
        action_run = cls._claim_due_action()
        if action_run is not None:
            parent_id = action_run.automation_run_id
            cls._execute_action_run(action_run)
            return AutomationRun.objects.filter(pk=parent_id).first()

        run = cls._claim_pending_run()
        if run is None:
            return None

        cls._plan_run(run)
        run.refresh_from_db()
        if run.status == AutomationRun.Status.RUNNING:
            action_run = cls._claim_due_action(automation_run_id=run.pk)
            if action_run is not None:
                cls._execute_action_run(action_run)
        run.refresh_from_db()
        return run

    @classmethod
    def next_poll_delay(cls, default=1.0):
        """Return a sleep interval that will not overshoot the next ActionRun.

        This keeps the 0.5 second default Action/Step boundary accurate even on
        older systemd units that still pass ``--poll-interval 1``.
        """
        try:
            default = max(float(default), 0.05)
        except (TypeError, ValueError):
            default = 1.0

        next_due = (
            ActionRun.objects
            .filter(
                status=AutomationRun.Status.PENDING,
                scheduled_for__isnull=False,
                automation_run__status=AutomationRun.Status.RUNNING,
            )
            .order_by("scheduled_for", "id")
            .values_list("scheduled_for", flat=True)
            .first()
        )
        if next_due is None:
            return default
        remaining = max((next_due - timezone.now()).total_seconds(), 0.0)
        return min(default, max(remaining, 0.05))

    @classmethod
    def _claim_pending_run(cls):
        with transaction.atomic():
            run = (
                AutomationRun.objects
                .select_for_update(skip_locked=True)
                .select_related("automation", "step")
                .filter(status=AutomationRun.Status.PENDING)
                .order_by("created_at", "id")
                .first()
            )
            if run is None:
                return None
            run.status = AutomationRun.Status.RUNNING
            run.started_at = run.started_at or timezone.now()
            run.save(update_fields=["status", "started_at"])
            return run

    @classmethod
    def _claim_due_action(cls, *, automation_run_id=None):
        now = timezone.now()
        with transaction.atomic():
            queryset = (
                ActionRun.objects
                .select_for_update(skip_locked=True)
                .select_related(
                    "automation_run",
                    "automation_run__automation",
                    "action",
                    "action__device",
                    "action__target_automation",
                    "action__step",
                )
                .filter(
                    status=AutomationRun.Status.PENDING,
                    scheduled_for__isnull=False,
                    scheduled_for__lte=now,
                    automation_run__status=AutomationRun.Status.RUNNING,
                )
            )
            if automation_run_id is not None:
                queryset = queryset.filter(automation_run_id=automation_run_id)

            action_run = queryset.order_by("scheduled_for", "id").first()
            if action_run is None:
                return None

            action_run.status = AutomationRun.Status.RUNNING
            action_run.started_at = now
            action_run.save(update_fields=["status", "started_at"])
            return action_run

    @classmethod
    def _plan_run(cls, run):
        run = AutomationRun.objects.select_related("automation", "step").get(pk=run.pk)
        automation = run.automation
        if automation is None:
            return cls._finish(run, False, "삭제된 자동화입니다.")
        if not run.automation_name:
            run.automation_name = automation.name
            run.save(update_fields=["automation_name"])

        # Idempotency: if planning already happened before a worker restart, do
        # not duplicate ActionRun rows.
        if run.action_runs.exists():
            return True, "이미 실행 계획이 생성되어 있습니다."

        actions, error = cls._collect_matched_actions(run)
        if error:
            return cls._finish(run, False, error)
        if not actions:
            return cls._finish(run, True, "실행 조건을 만족한 Step이 없습니다.")

        cursor = timezone.now()
        pending_rows = []
        for index, action in enumerate(actions):
            if action.delay_position == Action.DelayPosition.BEFORE and action.delay:
                cursor += timedelta(seconds=action.delay)

            pending_rows.append(
                ActionRun(
                    automation_run=run,
                    action=action,
                    step_order=action.step.order,
                    order=action.order,
                    status=AutomationRun.Status.PENDING,
                    scheduled_for=cursor,
                )
            )

            if action.delay_position == Action.DelayPosition.AFTER and action.delay:
                cursor += timedelta(seconds=action.delay)
            elif index < len(actions) - 1:
                next_action = actions[index + 1]
                # An explicit BEFORE delay owns the whole boundary; otherwise
                # every Action boundary, including Step -> Step, receives the
                # default 0.5 second gap.
                if not (
                    next_action.delay_position == Action.DelayPosition.BEFORE
                    and next_action.delay
                ):
                    cursor += timedelta(seconds=cls.DEFAULT_ACTION_DELAY)

        ActionRun.objects.bulk_create(pending_rows)
        run.message = f"{len(pending_rows)}개 동작 실행 대기 중"
        run.save(update_fields=["message"])
        return True, run.message

    @classmethod
    def _execute_action_run(cls, action_run):
        run = action_run.automation_run
        action = action_run.action

        if run.status != AutomationRun.Status.RUNNING:
            action_run.status = AutomationRun.Status.CANCELLED
            action_run.message = "상위 자동화가 더 이상 실행 중이 아닙니다."
            action_run.finished_at = timezone.now()
            action_run.save(update_fields=["status", "message", "finished_at"])
            return False, action_run.message

        if action is None:
            return cls._fail_action_run(action_run, "삭제된 동작입니다.")

        success, message = cls._execute_action(
            action,
            run,
            async_nested=True,
        )

        action_run.status = (
            AutomationRun.Status.SUCCESS
            if success
            else AutomationRun.Status.FAILED
        )
        action_run.message = message or ""
        action_run.finished_at = timezone.now()
        action_run.save(update_fields=["status", "message", "finished_at"])

        if not success:
            cls._cancel_remaining_actions(
                run,
                message="선행 동작 실패로 취소됨",
            )
            return cls._finish(run, False, message)

        # ``scheduled_for`` is initially calculated from the planned timeline.
        # If a real device command took longer than expected (HTTP, Tuya, WOL,
        # etc.), push every later action by the same amount so the configured
        # delay/default 0.5s gap is measured from actual completion, not merely
        # from the original plan timestamp.
        cls._shift_future_schedule_after_completion(action_run)

        # A successfully executed action is now the newest command for this
        # Device.  It supersedes delayed commands from OTHER AutomationRuns,
        # while preserving future actions intentionally belonging to this run.
        if action.device_id:
            DeviceControlSupersessionService.cancel_pending_for_device(
                action.device,
                exclude_automation_run_id=run.pk,
                message=(
                    f"자동화 #{run.pk}의 더 새로운 {action.device.name} 제어로 대체됨"
                ),
            )

        cls._finalize_if_complete(run)
        return True, message

    @classmethod
    def _shift_future_schedule_after_completion(cls, action_run):
        if not action_run.scheduled_for or not action_run.finished_at:
            return
        lateness = action_run.finished_at - action_run.scheduled_for
        if lateness.total_seconds() <= 0:
            return

        future_rows = list(
            ActionRun.objects.filter(
                automation_run_id=action_run.automation_run_id,
                status=AutomationRun.Status.PENDING,
                scheduled_for__gt=action_run.scheduled_for,
            ).order_by("scheduled_for", "id")
        )
        for future in future_rows:
            future.scheduled_for = future.scheduled_for + lateness
        if future_rows:
            ActionRun.objects.bulk_update(future_rows, ["scheduled_for"])

    @classmethod
    def _fail_action_run(cls, action_run, message):
        action_run.status = AutomationRun.Status.FAILED
        action_run.message = message
        action_run.finished_at = timezone.now()
        action_run.save(update_fields=["status", "message", "finished_at"])
        cls._cancel_remaining_actions(action_run.automation_run, message="선행 동작 실패로 취소됨")
        return cls._finish(action_run.automation_run, False, message)

    @classmethod
    def _cancel_remaining_actions(cls, run, *, message):
        ActionRun.objects.filter(
            automation_run=run,
            status=AutomationRun.Status.PENDING,
        ).update(
            status=AutomationRun.Status.CANCELLED,
            message=message,
            finished_at=timezone.now(),
        )

    @classmethod
    def _finalize_if_complete(cls, run):
        run = AutomationRun.objects.get(pk=run.pk)
        if run.status != AutomationRun.Status.RUNNING:
            return

        active = run.action_runs.filter(
            status__in=[AutomationRun.Status.PENDING, AutomationRun.Status.RUNNING]
        ).exists()
        if active:
            return

        statuses = list(run.action_runs.values_list("status", flat=True))
        if not statuses:
            return cls._finish(run, True, "실행 조건을 만족한 Step이 없습니다.")
        if AutomationRun.Status.FAILED in statuses:
            return cls._finish(run, False, "자동화 동작 중 실패가 발생했습니다.")
        if all(
            status in {AutomationRun.Status.CANCELLED, AutomationRun.Status.SKIPPED}
            for status in statuses
        ):
            run.status = AutomationRun.Status.CANCELLED
            run.message = "모든 대기 동작이 더 새로운 기기 제어로 대체되었습니다."
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "message", "finished_at"])
            return False, run.message

        cancelled_count = statuses.count(AutomationRun.Status.CANCELLED)
        message = (
            f"자동화 실행 완료 ({cancelled_count}개 기기 동작 대체됨)"
            if cancelled_count
            else "자동화 실행 완료"
        )
        return cls._finish(run, True, message)

    # ------------------------------------------------------------------
    # Shared graph/trigger helpers
    # ------------------------------------------------------------------
    @classmethod
    def _collect_matched_actions(cls, run):
        automation = run.automation
        if automation is None:
            return None, "삭제된 자동화입니다."

        if run.step_id:
            steps = [run.step]
        else:
            steps = list(
                automation.steps
                .filter(enabled=True)
                .prefetch_related(
                    "triggers",
                    "actions__device",
                    "actions__target_automation",
                )
                .order_by("order", "id")
            )

        if not steps:
            return None, "등록된 Step이 없습니다."

        flattened = []
        for step in steps:
            triggers = list(step.triggers.order_by("order", "id"))
            matched = AutomationService.trigger_list_matches(
                triggers,
                timezone.now(),
                trigger_payload=run.trigger_payload or {},
                operator=step.trigger_operator,
                resting=False,
                empty_matches=True,
            )
            if matched is not True:
                continue

            actions = list(step.actions.order_by("order", "id"))
            if not actions:
                return None, f"Step {step.order}에 실행 동작이 없습니다."
            flattened.extend(actions)

        return flattened, None

    # ------------------------------------------------------------------
    # Legacy synchronous compatibility path
    # ------------------------------------------------------------------
    @classmethod
    def execute_run(cls, run):
        if isinstance(run, int):
            run = AutomationRun.objects.select_related("automation", "step").get(pk=run)
        automation = run.automation
        if automation is None:
            return cls._finish(run, False, "삭제된 자동화입니다.")
        if not run.automation_name:
            run.automation_name = automation.name

        if run.status != AutomationRun.Status.RUNNING:
            run.status = AutomationRun.Status.RUNNING
            run.started_at = timezone.now()
            run.save(update_fields=["status", "started_at", "automation_name"])
        elif not run.started_at:
            run.started_at = timezone.now()
            run.save(update_fields=["started_at", "automation_name"])

        actions, error = cls._collect_matched_actions(run)
        if error:
            return cls._finish(run, False, error)
        if not actions:
            return cls._finish(run, True, "실행 조건을 만족한 Step이 없습니다.")

        try:
            for index, action in enumerate(actions):
                if action.delay_position == Action.DelayPosition.BEFORE and action.delay:
                    time.sleep(action.delay)

                action_run = ActionRun.objects.create(
                    automation_run=run,
                    action=action,
                    step_order=action.step.order,
                    order=action.order,
                    status=AutomationRun.Status.RUNNING,
                    scheduled_for=timezone.now(),
                    started_at=timezone.now(),
                )
                success, message = cls._execute_action(action, run, async_nested=False)
                action_run.status = AutomationRun.Status.SUCCESS if success else AutomationRun.Status.FAILED
                action_run.message = message or ""
                action_run.finished_at = timezone.now()
                action_run.save(update_fields=["status", "message", "finished_at"])
                if not success:
                    return cls._finish(run, False, message)

                if action.device_id:
                    DeviceControlSupersessionService.cancel_pending_for_device(
                        action.device,
                        exclude_automation_run_id=run.pk,
                    )

                if action.delay_position == Action.DelayPosition.AFTER and action.delay:
                    time.sleep(action.delay)
                elif index < len(actions) - 1:
                    next_action = actions[index + 1]
                    if not (
                        next_action.delay_position == Action.DelayPosition.BEFORE
                        and next_action.delay
                    ):
                        # Applies across Step boundaries too.
                        time.sleep(cls.DEFAULT_ACTION_DELAY)

            return cls._finish(run, True, "자동화 실행 완료")
        except Exception as exc:
            return cls._finish(run, False, f"자동화 실행 중 예외가 발생했습니다. ({exc})")

    @classmethod
    def _execute_action(cls, action, parent_run, *, async_nested=False):
        if action.action_type == Action.Type.AUTOMATION:
            target = action.target_automation
            if target is None:
                return False, "실행할 자동화가 지정되지 않았습니다."
            child_run = cls.enqueue(
                target,
                source=AutomationRun.Source.AUTOMATION,
                trigger_payload={
                    "parent_run_id": parent_run.pk,
                    "parent_action_id": action.pk,
                },
            )
            if async_nested:
                return True, f'"{target.name}" 자동화 실행 요청을 등록했습니다.'
            return cls.execute_run(child_run)

        if action.device is None or not action.function:
            return False, "기기 동작 정보가 올바르지 않습니다."
        try:
            return DeviceService.execute_step(action)
        except Exception as exc:
            return False, f"기기 동작 중 예외가 발생했습니다. ({exc})"

    @staticmethod
    def _finish(run, success, message):
        run.status = AutomationRun.Status.SUCCESS if success else AutomationRun.Status.FAILED
        run.message = message or ""
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "finished_at"])
        return success, message

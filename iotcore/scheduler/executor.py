import time

from django.db import transaction
from django.utils import timezone

from ..device.services.device_service import DeviceService
from ..models import Action, ActionRun, Automation, AutomationRun
from .service import AutomationService


class AutomationExecutor:
    """Single execution engine for immediate and scheduled Automations."""

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
        run = cls.enqueue(
            automation,
            source=source,
            trigger_payload=trigger_payload,
        )
        return cls.execute_run(run)

    @classmethod
    def run_next_pending(cls):
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
            run.started_at = timezone.now()
            run.save(update_fields=["status", "started_at"])
        cls.execute_run(run)
        return run

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

        if run.step_id:
            steps = [run.step]
        else:
            steps = list(
                automation.steps
                .filter(enabled=True)
                .prefetch_related("triggers", "actions__device", "actions__target_automation")
                .order_by("order", "id")
            )

        if not steps:
            return cls._finish(run, False, "등록된 Step이 없습니다.")

        try:
            executed_any = False
            for step in steps:
                triggers = list(step.triggers.order_by("order", "id"))
                # A scheduled queued run was already edge-checked by the service;
                # evaluate again with its event payload for deterministic safety.
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
                    return cls._finish(run, False, f"Step {step.order}에 실행 동작이 없습니다.")

                executed_any = True
                for index, action in enumerate(actions):
                    if action.delay_position == Action.DelayPosition.BEFORE and action.delay:
                        time.sleep(action.delay)

                    action_run = ActionRun.objects.create(
                        automation_run=run,
                        action=action,
                        step_order=step.order,
                        order=action.order,
                        status=AutomationRun.Status.RUNNING,
                        started_at=timezone.now(),
                    )
                    success, message = cls._execute_action(action, run)
                    action_run.status = AutomationRun.Status.SUCCESS if success else AutomationRun.Status.FAILED
                    action_run.message = message or ""
                    action_run.finished_at = timezone.now()
                    action_run.save(update_fields=["status", "message", "finished_at"])
                    if not success:
                        return cls._finish(run, False, message)

                    if action.delay_position == Action.DelayPosition.AFTER and action.delay:
                        time.sleep(action.delay)
                    elif index < len(actions) - 1:
                        next_action = actions[index + 1]
                        if not (next_action.delay_position == Action.DelayPosition.BEFORE and next_action.delay):
                            time.sleep(cls.DEFAULT_ACTION_DELAY)

            if not executed_any:
                return cls._finish(run, True, "실행 조건을 만족한 Step이 없습니다.")
            return cls._finish(run, True, "자동화 실행 완료")
        except Exception as exc:
            return cls._finish(run, False, f"자동화 실행 중 예외가 발생했습니다. ({exc})")

    @classmethod
    def _execute_action(cls, action, parent_run):
        if action.action_type == Action.Type.AUTOMATION:
            target = action.target_automation
            if target is None:
                return False, "실행할 자동화가 지정되지 않았습니다."
            # Nested execution is synchronous so parent success/failure is clear.
            child_run = cls.enqueue(
                target,
                source=AutomationRun.Source.AUTOMATION,
                trigger_payload={
                    "parent_run_id": parent_run.pk,
                    "parent_action_id": action.pk,
                },
            )
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

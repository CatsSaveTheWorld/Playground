import time

from django.db import transaction
from django.utils import timezone

from ...models import SequenceRun, SequenceStep, SequenceStepRun
from .device_service import DeviceService


class SequenceExecutor:
    DEFAULT_STEP_DELAY = 0.5

    @classmethod
    def execute(cls, sequence):
        """Execute immediately while recording the same history as a worker run."""
        sequence_run = SequenceRun.objects.create(
            sequence=sequence,
            sequence_name=sequence.name,
            trigger=SequenceRun.Trigger.MANUAL,
        )
        return cls.execute_run(sequence_run)

    @classmethod
    def execute_run(cls, sequence_run):
        if isinstance(sequence_run, int):
            sequence_run = SequenceRun.objects.select_related("sequence").get(
                pk=sequence_run
            )

        if sequence_run.sequence is None:
            sequence_name = sequence_run.sequence_name or "삭제된 시퀀스"
            return cls._finish_run(
                sequence_run,
                False,
                f'"{sequence_name}" 원본 시퀀스가 삭제되어 실행할 수 없습니다.',
            )

        if not sequence_run.sequence_name:
            sequence_run.sequence_name = sequence_run.sequence.name
            sequence_run.save(update_fields=["sequence_name"])

        if sequence_run.status != SequenceRun.Status.RUNNING:
            sequence_run.status = SequenceRun.Status.RUNNING
            sequence_run.started_at = timezone.now()
            sequence_run.save(update_fields=["status", "started_at"])

        steps = list(
            sequence_run.sequence.steps
            .select_related("device", "device__controller")
            .order_by("order")
        )

        try:
            for index, step in enumerate(steps):
                if step.delay_position == SequenceStep.BEFORE and step.delay > 0:
                    time.sleep(step.delay)

                step_run = SequenceStepRun.objects.create(
                    sequence_run=sequence_run,
                    sequence_step=step,
                    step_order=step.order,
                    action_code=step.function,
                    status=SequenceStepRun.Status.RUNNING,
                )

                try:
                    success, message = DeviceService.execute_step(step)
                except Exception as exc:
                    success = False
                    message = f"동작 실행 중 예외가 발생했습니다. ({exc})"

                step_run.status = (
                    SequenceStepRun.Status.SUCCESS
                    if success
                    else SequenceStepRun.Status.FAILED
                )
                step_run.message = message or ""
                step_run.finished_at = timezone.now()
                step_run.save(
                    update_fields=["status", "message", "finished_at"]
                )

                if not success:
                    return cls._finish_run(sequence_run, False, message)
                
                if index == len(steps) - 1:
                    continue

                if (
                    step.delay_position == SequenceStep.AFTER
                    and step.delay > 0
                ):
                    time.sleep(step.delay)
                else:
                    time.sleep(cls.DEFAULT_STEP_DELAY)

        except Exception as exc:
            return cls._finish_run(
                sequence_run,
                False,
                f"시퀀스 실행 중 예외가 발생했습니다. ({exc})",
            )

        return cls._finish_run(sequence_run, True, "시퀀스 실행 완료")

    @classmethod
    def run_next_pending(cls):
        with transaction.atomic():
            sequence_run = (
                SequenceRun.objects
                .select_for_update(skip_locked=True)
                .select_related("sequence")
                .filter(status=SequenceRun.Status.PENDING)
                .order_by("created_at")
                .first()
            )
            if sequence_run is None:
                return None
            sequence_run.status = SequenceRun.Status.RUNNING
            sequence_run.started_at = timezone.now()
            sequence_run.save(update_fields=["status", "started_at"])

        cls.execute_run(sequence_run)
        return sequence_run

    @staticmethod
    def _finish_run(sequence_run, success, message):
        sequence_run.status = (
            SequenceRun.Status.SUCCESS if success else SequenceRun.Status.FAILED
        )
        sequence_run.message = message or ""
        sequence_run.finished_at = timezone.now()
        sequence_run.save(
            update_fields=["status", "message", "finished_at"]
        )
        return success, message

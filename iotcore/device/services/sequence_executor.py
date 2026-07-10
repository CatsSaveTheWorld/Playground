import time

from .device_service import DeviceService
from ...models import SequenceStep

class SequenceExecutor:
    DEFAULT_STEP_DELAY = 0.7

    @staticmethod
    def execute(sequence):
        """
        Sequence를 순서대로 실행한다.
        """
        steps = list(
            sequence.steps
            .select_related("device", "device__controller")
            .order_by("order")
        )

        for index, step in enumerate(steps):

            # --------------------------
            # 동작 전 지연
            # --------------------------
            if (
                step.delay_position == SequenceStep.BEFORE
                and step.delay > 0
            ):
                time.sleep(step.delay)

            success, message = DeviceService.execute(step)

            if not success:
                return False, message

            # 마지막 Step은 대기하지 않음
            if index == len(steps) - 1:
                continue

            # --------------------------
            # 동작 후 지연
            # --------------------------
            if step.delay_position == SequenceStep.AFTER:
                if step.delay > 0:
                    time.sleep(step.delay)
                else:
                    time.sleep(SequenceExecutor.DEFAULT_STEP_DELAY)

        return True, "시퀀스 실행 완료"
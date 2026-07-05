import time

from .device_service import DeviceService

class SequenceExecutor:
    @staticmethod
    def execute(sequence):
        """
        Sequence를 순서대로 실행한다.
        """
        steps = (
            sequence.steps
            .select_related("device", "device__controller")
            .order_by("order")
        )
        for step in steps:
            success, message = DeviceService.execute(step)
            if not success:
                return False, message
            if step.delay:
                time.sleep(step.delay * 60)

        return True, "시퀀스 실행 완료"
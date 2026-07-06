import time

from .device_service import DeviceService


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

        # print(f"[DEBUG] steps : {steps}")

        for index, step in enumerate(steps):
            success, message = DeviceService.execute(step)

            if not success:
                return False, message

            # 마지막 Step은 기다릴 필요 없음
            if index == len(steps) - 1:
                continue

            # 사용자가 지연 시간을 지정한 경우
            if step.delay and step.delay > 0:
                time.sleep(step.delay * 60)

            # 지정하지 않았다면 기본 딜레이
            else:
                time.sleep(SequenceExecutor.DEFAULT_STEP_DELAY)

        return True, "시퀀스 실행 완료"
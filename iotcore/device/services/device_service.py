from ..repositories.controller_repository import ControllerRepository
from ..repositories.ircode_repository import IRCodeRepository
from ...infrastructure.ir.client import IRClient
from ...device.repositories.device_repository import DeviceRepository
from django.http import JsonResponse


class DeviceService:

    @staticmethod
    def execute(step):
        """
        SequenceStep 하나를 실행한다.
        모든 실행의 공통 진입점.
        """
        device = step.device
        dispatch = {
            "AIRCON": DeviceService.execute_aircon,
            "FAN": DeviceService.execute_fan,
            "LIGHT": DeviceService.execute_light,
        }
        executor = dispatch.get(device.device_type)

        if executor is None:
            return False, f"지원하지 않는 장치 타입입니다. ({device.device_type})"

        return executor(step)

    @staticmethod
    def control(device_id, motion, success_message=None, bits=None):

        device = DeviceRepository.get_by_id(device_id)
        if not device:
            return False, "기기를 찾을 수 없습니다."

        controller = ControllerRepository.get_controller_by_device(device_id)
        if not controller:
            return False, "연결된 컨트롤러를 찾을 수 없습니다."

        success, error_message = DeviceService.execute_ir(
            controller_id=controller.id,
            motion=motion,
            bits=bits,
        )

        if success:
            return True, success_message or f"{device.name} 제어를 완료했습니다."

        return False, error_message
    

    @staticmethod
    def execute_aircon(step):
        """
        detail.py 의 공통 제어 함수를 그대로 사용.
        """
        return DeviceService.control(
            device_id=step.device.id,
            motion=step.function,
        )

    @staticmethod
    def execute_fan(step):
        return DeviceService.control(
            device_id=step.device.id,
            motion=step.function,
        )

    @staticmethod
    def execute_light(step):
        return DeviceService.control(
            device_id=step.device.id,
            motion=step.function,
        )

    @staticmethod
    def execute_ir(controller_id, motion, bits=None):
        controller = ControllerRepository.get_controller(controller_id)
        if not controller:
            return False, "컨트롤러를 찾을 수 없습니다."

        code = IRCodeRepository.get_ir_code(motion, bits)
        if not code:
            return False, f"'{motion}'에 대한 IR 코드가 없습니다."

        success = IRClient.send_ir_request(
            controller.ip_address,
            code,
        )

        if not success:
            return False, "ESP32와 통신에 실패했습니다."

        return True, "정상적으로 제어되었습니다."

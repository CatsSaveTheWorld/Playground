from ..repositories.controller_repository import ControllerRepository
from ..repositories.ircode_repository import IRCodeRepository
from ...infrastructure.ir.client import IRClient
from ...device.repositories.device_repository import DeviceRepository
from django.http import JsonResponse
from ...infrastructure.zigbee.client import ZigbeeClient


class DeviceService:

    @staticmethod
    def execute_step(step):
        """
        SequenceStep 하나를 실행한다.
        모든 실행의 공통 진입점.
        """
        device = step.device
        dispatch = {
            "aircon": DeviceService.execute_aircon,
            "fan": DeviceService.execute_fan,
            "light": DeviceService.execute_light,
        }
        executor = dispatch.get(device.device_type)
        
        # print(f"[DEBUG] DeviceService.executor 내부 dispatch : {dispatch}")
        # print(f"[DEBUG] DeviceService.executor 결과 executor : {executor}")
        # print(f"[DEBUG] DeviceService.executor 결과 device_type : {device.device_type}")

        if executor is None:
            return False, f"지원하지 않는 장치 타입입니다. ({device.device_type})"

        return executor(step)
        # return True, f"{step}을 성공적으로 수행했습니다."

    @staticmethod
    def control(device_id, motion, success_message=None, bits=None) -> tuple:

        device = DeviceRepository.get_by_id(device_id)
        print(f"[DEBUG] control device : {device}")

        if not device:
            return False, "기기를 찾을 수 없습니다."

        if device.protocol == 'ir':
            controller = ControllerRepository.get_controller_by_device(device_id)
            print(f"[DEBUG] control controller : {controller}")

            if not controller:
                return False, "연결된 컨트롤러를 찾을 수 없습니다."

            success, error_message = DeviceService.execute_ir(
                controller_id=controller.id,
                motion=motion,
                bits=bits,
            )            
            print(f"[DEBUG] control success : {success}")
            print(f"[DEBUG] control error_message : {error_message}")

        elif device.protocol == 'tuya':
            pass

        elif device.protocol == 'zigbee':
            print(f"[DEBUG] device.protocol : zigbee")
            success, error_message = DeviceService.execute_light(
                device_id=device_id,
                motion=motion,
            )

        if success:
            return True, success_message or f"{device.name} 제어를 완료했습니다."

        return False, error_message
    

    @staticmethod
    def execute_aircon(step):
        """
        detail.py 의 공통 제어 함수를 그대로 사용.
        """
        print(f"[DEBUG] execute_aircon device_id : {step.device.id}")
        print(f"[DEBUG] execute_aircon motion : {step.function}")
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
    def execute_light(device_id, motion):

        device = DeviceRepository.get_by_id(device_id)

        if not device:
            return False, "기기를 찾을 수 없습니다."

        mapping = {
            "power_on": "ON",
            "power_off": "OFF",
        }

        state = mapping.get(motion)
        # print(f"[DEBUG] state : {state}")


        if state is None:
            return False, f"지원하지 않는 동작입니다. ({motion})"

        return ZigbeeClient.send_zigbee_request(
            device.device_uid,
            state
        )
    

    @staticmethod
    def execute_ir(controller_id, motion, bits=None):
        controller = ControllerRepository.get_controller(controller_id)
        print(f"[DEBUG] execute_ir controller : {controller}")

        if not controller:
            return False, "컨트롤러를 찾을 수 없습니다."

        code = IRCodeRepository.get_ir_code(motion, bits)
        print(f"[DEBUG] execute_ir code : {code}")

        if not code:
            return False, f"'{motion}'에 대한 IR 코드가 없습니다."

        success = IRClient.send_ir_request(
            controller.ip_address,
            code,
        )
        print(f"[DEBUG] execute_ir success : {success}")

        if not success:
            return False, "ESP32와 통신에 실패했습니다."

        return True, "정상적으로 제어되었습니다."

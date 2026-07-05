from ..repositories.controller_repository import ControllerRepository
from ..repositories.ircode_repository import IRCodeRepository
from ...infrastructure.ir.client import IRClient


class DeviceService:

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
            code
        )

        if not success:
            return False, "ESP32와 통신에 실패했습니다."

        return True, "정상적으로 제어되었습니다."
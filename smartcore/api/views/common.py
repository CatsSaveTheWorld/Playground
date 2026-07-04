import json
from django.http import JsonResponse
from ...device.repositories.controller_repository import ControllerRepository
from ...device.services.device_service import DeviceService



def control_internal(controller_id, motion, success_message=None, bits=None):
    """
    공통 제어 함수

    Args:
        controller_id (int): 컨트롤러 ID
        motion (str): IR 동작명 (power_on, temp_up ...)
        success_message (str, optional): 성공 시 표시할 메시지
        bits (int, optional): IR 비트 수
    """

    controller = ControllerRepository.get_controller(controller_id)

    if not controller:
        return JsonResponse({
            "status": "error",
            "message": "컨트롤러를 찾을 수 없습니다."
        })

    success, error_message = DeviceService.execute_ir(
        controller_id=controller_id,
        motion=motion,
        bits=bits
    )

    if success:
        return JsonResponse({
            "status": "success",
            "message": success_message or f"{controller.device.name} 제어를 완료했습니다."
        })

    return JsonResponse({
        "status": "error",
        "message": error_message
    })


# 요청 데이터 자동 파싱 (Form / JSON 통합)
def parse_request_data(request):
    """Form-data 또는 JSON 요청을 dict로 변환"""
    if request.content_type == "application/json":
        try:
            return json.loads(request.body)
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()
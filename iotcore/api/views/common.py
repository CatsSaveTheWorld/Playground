import json
from django.http import JsonResponse
from ...device.repositories.device_repository import DeviceRepository
from ...device.repositories.controller_repository import ControllerRepository
from ...device.services.device_service import DeviceService



def control_internal(device_id, motion, success_message=None, bits=None):

    device = DeviceRepository.get_by_id(device_id)

    if not device:
        return JsonResponse({
            "status": "error",
            "message": "기기를 찾을 수 없습니다."
        })

    controller = ControllerRepository.get_controller_by_device(device_id)

    if not controller:
        return JsonResponse({
            "status": "error",
            "message": "연결된 컨트롤러를 찾을 수 없습니다."
        })

    success, error_message = DeviceService.execute_ir(
        controller_id=controller.id,
        motion=motion,
        bits=bits
    )

    if success:
        return JsonResponse({
            "status": "success",
            "message": success_message or f"{device.name} 제어를 완료했습니다."
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
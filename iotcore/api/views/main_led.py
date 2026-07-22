from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .common import parse_request_data
from ...device.repositories.device_repository import DeviceRepository
from ...device.services.device_service import DeviceService


motion_messages = {
    "power_on": "전등이 켜졌습니다!",
    "power_off": "전등이 꺼졌습니다!",
}


# ────────────────────────────────
#  통합 제어 엔트리 (Form + JSON)
# ────────────────────────────────
@csrf_exempt
def main_led_entry(request):
    # POST 요청만 허용
    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "message": "POST 요청만 허용됩니다."
            },
            status=400
        )

    # 요청 데이터 파싱
    data = parse_request_data(request)
    print(f"[DEBUG] data : {data}")

    device_id = data.get("device_id")
    motion = data.get("motion") or data.get("function")

    print(f"[DEBUG] device_id : {device_id}")
    print(f"[DEBUG] motion : {motion}")

    if not device_id:
        return JsonResponse(
            {
                "success": False,
                "message": "device_id 누락"
            },
            status=400
        )

    # Device 조회
    device = DeviceRepository.get_by_id(device_id)
    print(f"[DEBUG] device : {device}")

    if not device:
        return JsonResponse(
            {
                "success": False,
                "message": f"존재하지 않는 device_id: {device_id}"
            },
            status=404
        )

    if not motion:
        return JsonResponse(
            {
                "success": False,
                "message": "motion/function 값이 없습니다."
            },
            status=400
        )

    # 성공 메시지 결정
    success_message = motion_messages.get(
        motion,
        "요청된 동작을 수행했습니다."
    )
    print(f"[DEBUG] success_message : {success_message}")

    print(f"[DEBUG] 서비스 호출 시작")
    # 서비스 호출
    success, message = DeviceService.control(
        device_id=device.id,
        motion=motion,
        success_message=success_message,
    )
    print(f"[DEBUG] 서비스 호출 완료")
    print(f"[DEBUG] success : {success}")
    print(f"[DEBUG] message : {message}")


    # View는 반드시 JsonResponse를 반환
    return JsonResponse(
        {
            "success": success,
            "message": message,
        },
        status=200 if success else 400
    )


# ────────────────────────────────
#  전등 제어 함수 (AI 요청용, 미구현)
# ────────────────────────────────
def main_led_command(device_id, function):
    """
    function 값에 따라 에어컨 제어 수행
    """
    mapping = {
        "power_on": ("power_on", motion_messages["power_on"]),
        "power_off": ("power_off", motion_messages["power_off"]),
    }

    if function.startswith("set_temp_"):
        temp = function.split("_")[-1]
        motion = f"set_temp_{temp}"

        return DeviceService.control(
            device_id,
            motion,
            f"에어컨 온도가 {temp}°C로 설정되었습니다."
        )

    if function not in mapping:
        print("[DEBUG] 제어 함수가 제어 사전(맵)에 없음.")
        print(f"[DEBUG] function : {function}")
        return JsonResponse(
            {
                "status": "fail",
                "message": f"지원되지 않는 기능: {function}"
            },
            status=400
        )

    motion, message = mapping[function]

    print("[DEBUG] Main LED 제어 실행.")

    return DeviceService.control(
        device_id,
        motion,
        message
    )



# ────────────────────────────────
#  전등 호환용 뷰 (기존 웹 요청 URL 유지)
# ────────────────────────────────
@csrf_exempt
def main_led_power_on(request):
    print(f"main_led_power_on 요청 완료.")
    request.POST = request.POST.copy()
    request.POST['motion'] = 'power_on'
    return main_led_entry(request)

@csrf_exempt
def main_led_power_off(request):
    print(f"main_led_power_off 요청 완료.")
    request.POST = request.POST.copy()
    request.POST['motion'] = 'power_off'
    return main_led_entry(request)
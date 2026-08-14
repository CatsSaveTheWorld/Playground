from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from ...models import Device
from .common import parse_request_data
from ...device.services.device_service import DeviceService

motion_messages = {
    "power_on": "에어컨 전원이 켜졌습니다!",
    "power_off": "에어컨 전원이 꺼졌습니다!",
    "mode_auto": "에어컨이 자동 모드로 설정되었습니다!",
    "mode_cool": "에어컨이 냉방 모드로 설정되었습니다!",
    "mode_dehumidification": "에어컨이 제습 모드로 설정되었습니다!",
    "mode_fan": "에어컨이 송풍 모드로 설정되었습니다!",
}


# ────────────────────────────────
#  통합 제어 엔트리 (Form + JSON)
# ────────────────────────────────
def aircon_entry(request):
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
    device = Device.objects.filter(id=device_id).first()
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

    # 서비스 호출
    success, message = DeviceService.control(
        device_id=device.id,
        motion=motion,
        success_message=success_message,
    )

    # View는 반드시 JsonResponse를 반환
    return JsonResponse(
        {
            "success": success,
            "message": message,
        },
        status=200 if success else 400
    )


# ────────────────────────────────
#  에어컨 호환용 뷰 (기존 웹 요청 URL 유지)
# ────────────────────────────────
@login_required(login_url="common:login")
@require_POST
def aircon_power_on(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'power_on'
    return aircon_entry(request)


@login_required(login_url="common:login")
@require_POST
def aircon_power_off(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'power_off'
    return aircon_entry(request)


@login_required(login_url="common:login")
@require_POST
def aircon_mode_auto(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_auto'
    return aircon_entry(request)


@login_required(login_url="common:login")
@require_POST
def aircon_mode_cool(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_cool'
    return aircon_entry(request)


@login_required(login_url="common:login")
@require_POST
def aircon_mode_fan(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_fan'
    return aircon_entry(request)


@login_required(login_url="common:login")
@require_POST
def aircon_dehumidification_mode(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_dehumidification'
    return aircon_entry(request)


@login_required(login_url="common:login")
@require_POST
def aircon_set_temp(request):
    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "message": "POST 요청만 허용됩니다."
            },
            status=400
        )

    data = parse_request_data(request)

    device_id = data.get("device_id")
    temperature = data.get("temperature")

    if not device_id or not temperature:
        return JsonResponse(
            {
                "success": False,
                "message": "device_id 또는 temperature 값이 없습니다."
            },
            status=400
        )

    if not str(temperature).isdigit():
        return JsonResponse(
            {
                "success": False,
                "message": "유효한 온도 값이 아닙니다. (숫자만 가능)"
            },
            status=400
        )

    motion = f"set_temp_{temperature}"
    success_message = f"에어컨 온도가 {temperature}°C로 설정되었습니다."

    success, message = DeviceService.control(
        device_id=device_id,
        motion=motion,
        success_message=success_message,
    )

    return JsonResponse(
        {
            "success": success,
            "message": message,
        },
        status=200 if success else 400,
    )

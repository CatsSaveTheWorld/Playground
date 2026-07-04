from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from ...models import Controller
from .common import parse_request_data, control_internal

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
@csrf_exempt
def aircon_entry(request):
    # get 요청 아닌 것들 거르기
    if request.method != "POST":
        return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)

    data = parse_request_data(request)
    controller_id = data.get("controller_id")
    motion = data.get("motion") or data.get("function")

    print(f"[DEBUG] controller_id : {controller_id}")
    print(f"[DEBUG] motion : {motion}")

    if not controller_id:
        return JsonResponse({'status': 'fail', 'message': 'controller_id 누락'}, status=400)

    # 🔹 1️⃣ Controller 조회
    controller = Controller.objects.filter(id=controller_id).select_related("device").first()
    if not controller:
        return JsonResponse({'status': 'fail', 'message': f'존재하지 않는 controller_id: {controller_id}'}, status=404)

    # 🔹 2️⃣ Controller에서 필요한 정보 추출
    device = controller.device
    location = controller.location
    print(f"[DEBUG] Controller 연결 정보 → device: {device}, location: {location}")

    if not motion:
        return JsonResponse({'status': 'fail', 'message': 'motion/function 값이 없습니다.'}, status=400)

    # 🔹 3️⃣ 실제 제어 요청 수행
    success_message = motion_messages.get(motion, "요청된 동작을 수행했습니다.")
    return control_internal(
        controller.id,
        motion,
        success_message
    )


# ────────────────────────────────
#  에어컨 제어 함수 (AI 요청용)
# ────────────────────────────────
def handle_aircon_command(controller_id, function):
    """
    function 값에 따라 에어컨 제어 수행
    """
    mapping = {
        "power_on": ("power_on", motion_messages["power_on"]),
        "power_off": ("power_off", motion_messages["power_off"]),
        "mode_auto": ("mode_auto", motion_messages["mode_auto"]),
        "mode_cool": ("mode_cool", motion_messages["mode_cool"]),
        "mode_dehumidification": ("mode_dehumidification", motion_messages["mode_dehumidification"]),
        "mode_fan": ("mode_fan", motion_messages["mode_fan"]),
    }

    # set_temp_xx 형태일 경우
    if function.startswith("set_temp_"):
        temp = function.split("_")[-1]
        motion = f"set_temp_{temp}"
        return control_internal(
            controller_id,
            motion,
            f"에어컨 온도가 {temp}°C로 설정되었습니다."
        )

    # 일반적인 명령 처리
    if function not in mapping:
        print("[DEBUG] 제어 함수가 제어 사전(맵)에 없음.")
        print(f"[DEBUG] function : {function}")
        return JsonResponse({"status": "fail", "message": f"지원되지 않는 기능: {function}"}, status=400)

    motion, message = mapping[function]
    print("[DEBUG] 에어컨 제어 실행.")
    return control_internal(controller_id, motion, message)

# ────────────────────────────────
#  에어컨 호환용 뷰 (기존 웹 요청 URL 유지)
# ────────────────────────────────
@csrf_exempt
def aircon_power_on(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'power_on'
    return aircon_entry(request)


@csrf_exempt
def aircon_power_off(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'power_off'
    return aircon_entry(request)


@csrf_exempt
def aircon_mode_auto(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_auto'
    return aircon_entry(request)


@csrf_exempt
def aircon_mode_cool(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_cool'
    return aircon_entry(request)


@csrf_exempt
def aircon_mode_fan(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_fan'
    return aircon_entry(request)


@csrf_exempt
def aircon_dehumidification_mode(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'mode_dehumidification'
    return aircon_entry(request)


@csrf_exempt
def aircon_set_temp(request):
    if request.method != "POST":
        return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)

    data = parse_request_data(request)
    controller_id = data.get("controller_id")
    temperature = data.get("temperature")

    if not controller_id or not temperature:
        return JsonResponse({'status': 'fail', 'message': 'controller_id 또는 temperature 값이 없습니다.'}, status=400)

    if not str(temperature).isdigit():
        return JsonResponse({'status': 'fail', 'message': '유효한 온도 값이 아닙니다. (숫자만 가능)'}, status=400)

    motion = f"set_temp_{temperature}"
    success_message = f"에어컨 온도가 {temperature}°C로 설정되었습니다."
    return control_internal(controller_id, motion, success_message)
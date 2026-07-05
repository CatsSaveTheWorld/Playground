from django.http import JsonResponse
from ...models import Controller, Device
from django.views.decorators.csrf import csrf_exempt

from ...infrastructure.ir.client import IRClient
from ...device.repositories.controller_repository import ControllerRepository
from .common import parse_request_data, control_internal


motion_messages = {
    "power_on": "선풍기 전원이 켜졌습니다!",
    "power_off": "선풍기 전원이 꺼졌습니다!",
}


# ────────────────────────────────
#  선풍기 내부 제어 함수 (공통)
# ────────────────────────────────
def electricfan_control_internal(controller_id, motion, success_message, bits=None):
    controller = ControllerRepository.get_controller(controller_id)
    if not controller:
        return JsonResponse({'status': 'fail', 'message': "컨트롤러 없음"}, status=404)

    success, result = IRClient.send_ir_request(controller.ip_address, motion)
    if success:
        return JsonResponse({'status': 'success', 'message': success_message})
    else:
        return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류: {result}"}, status=500)

# ────────────────────────────────
#  선풍기 통합 제어 엔트리
# ────────────────────────────────
def electricfan_entry(request):
    print(f"electricfan_entry 요청 완료.")
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
    target = controller.device
    location = controller.location
    print(f"[DEBUG] Controller 연결 정보 → target: {target}, location: {location}")

    if not motion:
        return JsonResponse({'status': 'fail', 'message': 'motion/function 값이 없습니다.'}, status=400)

    # 🔹 3️⃣ 실제 제어 요청 수행
    success_message = motion_messages.get(motion, "요청된 동작을 수행했습니다.")
    return electricfan_control_internal(controller.id, motion, success_message)

# ────────────────────────────────
#  선풍기 호환용 뷰 (기존 웹 요청 URL 유지)
# ────────────────────────────────
@csrf_exempt
def electricfan_power_cycle(request):
    print(f"electricfan_power_cycle 요청 완료.")
    request.POST = request.POST.copy()
    request.POST['motion'] = 'power_cycle'
    return electricfan_entry(request)


@csrf_exempt
def electricfan_stop(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'stop'
    return electricfan_entry(request)


@csrf_exempt
def electricfan_fan_way_toggle(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'fan_way_toggle'
    return electricfan_entry(request)


@csrf_exempt
def electricfan_timer_add_30m(request):
    request.POST = request.POST.copy()
    request.POST['motion'] = 'timer_add_30m'
    return electricfan_entry(request)
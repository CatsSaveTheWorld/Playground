from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from ...models import Controller, Device
from .common import parse_request_data
from .aircon import handle_aircon_command

# ────────────────────────────────
#  AI 요청 처리
# ────────────────────────────────
@csrf_exempt
def ai_control(request):
    """
    AI PC에서 단일 POST 요청을 받아 device 종류에 따라 분기 처리
    현재는 aircon(에어컨)만 지원
    """
    if request.method != "POST":
        return JsonResponse({"status": "fail", "message": "POST 요청만 허용됩니다."}, status=400)

    data = parse_request_data(request)
    print(f"[DEBUG] TTS > JSON 데이터 수신")
    print(f"[DEBUG] JSON 데이터 : {data}")
    
    device_type = data.get("device_type")    # ex) aircon
    function = data.get("function")
    location = data.get("location")

    print(f"[DEBUG] device_type : {device_type}")
    print(f"[DEBUG] function : {function}")
    print(f"[DEBUG] location : {location}")

    if not all([device_type, function, location]):
        return JsonResponse({
            "status": "fail",
            "message": "device, function, location이 모두 필요합니다."
        }, status=400)

    print("[DEBUG] controller 조회 시작")
    try:
        # 1️⃣ device_name (문자열)에 해당하는 Device 객체 찾기
        device_obj = Device.objects.get(device_type=device_type, location=location)
        controller = Controller.objects.filter(device=device_obj).first()

    except Device.DoesNotExist:
        return JsonResponse({
            "status": "fail",
            "message": f"{device_type} 기기를 찾을 수 없습니다."
        }, status=404)

    except Controller.DoesNotExist:
        return JsonResponse({
            "status": "fail",
            "message": f"{location}의 {device_type} 컨트롤러를 찾을 수 없습니다."
        }, status=404)

    controller_id = controller.id
    print("[DEBUG] controller 조회 끝")

    # ─────────────────────────────
    #  장치 종류별 분기 (현재 aircon만)
    # ─────────────────────────────
    if device_type == "aircon":
        print("[DEBUG] 에어컨 핸들 커맨드 실행.")
        return handle_aircon_command(controller_id, function)

    # 앞으로 다른 기기(e.g. electric_fan, light, smartthings 등)가 추가될 경우:
    # elif device_type == "electric_fan":
    #     return handle_fan_command(controller_id, function)
    # elif device_type == "light":
    #     return handle_light_command(controller_id, function)
    # elif device_type == "smartthings":
    #     return handle_smartthings_command(controller_id, function)

    else:
        return JsonResponse({
            "status": "fail",
            "message": f"지원되지 않는 device: {device_type}"
        }, status=400)
    

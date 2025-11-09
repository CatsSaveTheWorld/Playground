from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from ..models import Controller, Device
from django.conf import settings
import pandas as pd
import requests
import os
import json

# CSV 파일 경로 설정
DATA_DIR = os.path.join(settings.BASE_DIR, 'smartcore', 'management', 'data')
wifi_path = os.path.join(DATA_DIR, 'wifi.csv').replace('\\', '/')
device_path = os.path.join(DATA_DIR, 'device_codes.csv').replace('\\', '/')

# CSV 데이터 로드
wifi = pd.read_csv(wifi_path)
wifi_ssid, wifi_password = wifi['ssid'][0], wifi['password'][0]
device = pd.read_csv(device_path, encoding='utf-8')
device.bits = device.bits.astype(int)


def detail_list(request):
    return render(request, "smartcore/detail_list.html")

motion_messages = {
    "power_on": "에어컨 전원이 켜졌습니다!",
    "power_off": "에어컨 전원이 꺼졌습니다!",
    "mode_auto": "에어컨이 자동 모드로 설정되었습니다!",
    "mode_cool": "에어컨이 냉방 모드로 설정되었습니다!",
    "mode_dehumidification": "에어컨이 제습 모드로 설정되었습니다!",
    "mode_fan": "에어컨이 송풍 모드로 설정되었습니다!",
}

# ────────────────────────────────
#  공통 유틸
# ────────────────────────────────
def get_controller(controller_id):
    try:
        return Controller.objects.get(id=controller_id)
    except Controller.DoesNotExist:
        return None


def get_ir_code(motion, bits=24):
    query = (device.motion == motion)
    if bits:
        query &= (device.bits == bits)
    try:
        return device.loc[query, 'code'].iloc[0]
    except IndexError:
        return None


def send_ir_request(ip_address, code):
    url = f"http://{ip_address}/ir?code={code}"
    print(f"ESP32로 전송될 URL: {url}")
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        print(f"ESP32 응답: {res.text}")
        return True, res.text
    except requests.exceptions.RequestException as e:
        print(f"ESP32 통신 오류: {e}")
        return False, str(e)


# 요청 데이터 자동 파싱 (Form / JSON 통합)
def parse_request_data(request):
    """Form-data 또는 JSON 요청을 dict로 변환"""
    if request.content_type == "application/json":
        try:
            return json.loads(request.body)
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()


# ────────────────────────────────
#  내부 제어 함수 (공통)
# ────────────────────────────────
def aircon_control_internal(controller_id, motion, success_message, bits=None):
    controller = get_controller(controller_id)
    if not controller:
        return JsonResponse({'status': 'fail', 'message': "컨트롤러 없음"}, status=404)

    code = get_ir_code(motion, bits)
    if not code:
        return JsonResponse({'status': 'fail', 'message': f"{motion} 코드가 존재하지 않습니다."}, status=404)

    success, result = send_ir_request(controller.ip_address, code)
    if success:
        return JsonResponse({'status': 'success', 'message': success_message})
    else:
        return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류: {result}"}, status=500)


# ────────────────────────────────
#  통합 제어 엔트리 (Form + JSON)
# ────────────────────────────────
@csrf_exempt
def aircon_entry(request):
    """
    웹페이지(Form)와 AI PC(JSON) 요청을 모두 처리하는 통합 엔드포인트
    """
    if request.method != "POST":
        return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)

    data = parse_request_data(request)
    print(f"[DEBUG] aircon_entry 데이터: {data}")

    controller_id = data.get("controller_id")
    motion = data.get("motion") or data.get("function")

    # controller_id가 없으면 device/location으로 탐색 (AI 요청용)
    if not controller_id:
        device_name = data.get("device")
        location = data.get("location")
        if not (device_name and location):
            return JsonResponse({'status': 'fail', 'message': 'controller_id 또는 device/location 정보가 필요합니다.'}, status=400)

        try:
            controller = Controller.objects.get(device=device_name, location=location)
            controller_id = controller.id
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message': f"{location}의 {device_name} 컨트롤러를 찾을 수 없습니다."}, status=404)

    # motion(혹은 function)이 비어있을 경우 예외 처리
    if not motion:
        return JsonResponse({'status': 'fail', 'message': 'motion 또는 function 값이 필요합니다.'}, status=400)

    # 동작 수행
    success_message = motion_messages[motion]
    return aircon_control_internal(controller_id, motion, success_message)


# ────────────────────────────────
#  호환용 뷰 (기존 웹 요청 URL 유지)
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
    return aircon_control_internal(controller_id, motion, success_message)


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

    if not all([device_type, function, location]):
        return JsonResponse({
            "status": "fail",
            "message": "device, function, location이 모두 필요합니다."
        }, status=400)

    print("[DEBUG] controller 조회 시작")
    try:
        # 1️⃣ device_name (문자열)에 해당하는 Device 객체 찾기
        device_obj = Device.objects.get(device_type=device_type, location=location)
        controller = device_obj.controller

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


# ────────────────────────────────
#  에어컨 제어 함수 (AI 요청용)
# ────────────────────────────────
def handle_aircon_command(controller_id, function):
    """
    function 값에 따라 에어컨 제어 수행
    """
    mapping = {
        "power_on": ("power_on", "에어컨 전원이 켜졌습니다!"),
        "power_off": ("power_off", "에어컨 전원이 꺼졌습니다!"),
        "mode_auto": ("mode_auto", "에어컨 자동 모드로 설정되었습니다!"),
        "mode_cool": ("mode_cool", "에어컨 냉방 모드로 설정되었습니다!"),
        "mode_dehumidification": ("mode_dehumidification", "에어컨 제습 모드로 설정되었습니다!"),
        "mode_fan": ("mode_fan", "에어컨 송풍 모드로 설정되었습니다!"),
    }

    # set_temp_xx 형태일 경우
    if function.startswith("set_temp_"):
        temp = function.split("_")[-1]
        motion = f"set_temp_{temp}"
        return aircon_control_internal(
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
    return aircon_control_internal(controller_id, motion, message)

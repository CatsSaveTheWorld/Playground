from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from ..models import Controller
from django.conf import settings
import pandas as pd
import requests
import os

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


def validate_post_request(request, keys):
    for key in keys:
        if key not in request.POST or not request.POST.get(key):
            return False, JsonResponse({'status': 'fail', 'message': f"{key} 값이 전송되지 않았습니다."}, status=400)
    return True, None


@csrf_exempt
def aircon_control(request, motion, success_message, bits=None):
    if request.method != "POST":
        return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)

    is_valid, error_response = validate_post_request(request, ['controller_id'])
    if not is_valid:
        return error_response

    controller = get_controller(request.POST.get("controller_id"))
    if not controller:
        return JsonResponse({'status': 'fail', 'message': "컨트롤러 없음"}, status=404)

    code = get_ir_code(motion, bits)
    if not code:
        return JsonResponse({'status': 'fail', 'message': f"{motion} 코드가 존재하지 않습니다."}, status=404)

    success, result = send_ir_request(controller.ip_address, code)
    if success:
        return JsonResponse({'status': 'success', 'message': success_message})
    else:
        return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류가 발생했습니다: {result}"}, status=500)


@csrf_exempt
def aircon_power_on(request):
    return aircon_control(request, 'power_on', '에어컨 전원이 켜졌습니다!')


@csrf_exempt
def aircon_power_off(request):
    return aircon_control(request, 'power_off', '에어컨 전원이 꺼졌습니다!')


@csrf_exempt
def aircon_mode_auto(request):
    return aircon_control(request, 'mode_auto', '에어컨 자동 모드로 설정되었습니다!')


@csrf_exempt
def aircon_mode_cool(request):
    return aircon_control(request, 'mode_cool', '에어컨 냉방 모드로 설정되었습니다!')


@csrf_exempt
def aircon_dehumidification_mode(request):
    return aircon_control(request, 'mode_dehumidification', '에어컨 제습 모드로 설정되었습니다!')


@csrf_exempt
def aircon_mode_fan(request):
    return aircon_control(request, 'mode_fan', '에어컨 송풍 모드로 설정되었습니다!')


@csrf_exempt
def aircon_set_temp(request):
    if request.method != "POST":
        return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)

    is_valid, error_response = validate_post_request(request, ['controller_id', 'temperature'])
    if not is_valid:
        return error_response

    temperature = request.POST.get("temperature")
    if not temperature.isdigit():
        return JsonResponse({'status': 'fail', 'message': "유효한 온도 값이 전송되지 않았습니다. (숫자만 가능)"}, status=400)

    controller = get_controller(request.POST.get("controller_id"))
    if not controller:
        return JsonResponse({'status': 'fail', 'message': "컨트롤러 없음"}, status=404)

    motion = f"set_temp_{temperature}"
    code = get_ir_code(motion, bits=24)
    if not code:
        return JsonResponse({'status': 'fail', 'message': f"{temperature}도 코드가 존재하지 않습니다."}, status=404)

    success, result = send_ir_request(controller.ip_address, code)
    if success:
        return JsonResponse({'status': 'success', 'message': f'에어컨 온도가 {temperature}°C로 설정되었습니다.'})
    else:
        return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류가 발생했습니다: {result}"}, status=500)

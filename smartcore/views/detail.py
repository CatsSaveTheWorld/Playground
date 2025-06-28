from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from ..models import Controller  # 모델 import
from django.conf import settings
import pandas as pd
import requests
import os


wifi_path = os.path.join(settings.BASE_DIR, 'smartcore', 'management', 'data', 'wifi.csv').replace('\\', '/')
aircon_path = os.path.join(settings.BASE_DIR, 'smartcore', 'management', 'data', 'aircon_control_code.csv').replace('\\', '/')


wifi = pd.read_csv(wifi_path)
wifi_ssid, wifi_password = wifi['ssid'][0], wifi['password'][0]
aircon = pd.read_csv(aircon_path, encoding='utf-8')
aircon.bits = aircon.bits.astype(int)


# Create your views here.
def detail_list(request):
    return render(request, "smartcore/detail_list.html")


@csrf_exempt
def aircon_power_on(request):
    if request.method == "POST":
        # 여기에 실제 동작 넣기 (ex. 기기 제어 코드)
        controller_id = request.POST.get("controller_id")
        print(f"controller_id: {controller_id}")
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"}, status=400)
        
        try:
            code = aircon.loc[aircon.motion == 'power_on', 'code'].iloc[0] # 'motion' 컬럼이 'power_on'인 행의 'code'
        except IndexError: # 해당하는 motion이 없을 경우
            return JsonResponse({'status': 'fail', 'message': "에어컨 전원 켜기 코드를 찾을 수 없습니다."}, status=404)
        
        url = f"http://{controller.ip_address}/ir?code={code}"
        print(f"ESP32로 전송될 URL (Power ON): {url}") # 디버깅을 위한 로그

        try:
            res = requests.get(url, timeout=5) # timeout 추가
            res.raise_for_status() # HTTP 에러 발생 시 예외 발생
            print(f"ESP32 응답: {res.text}")
            return JsonResponse({
                'status': 'success',
                'message': '에어컨 전원이 켜졌습니다!'
            })
        except requests.exceptions.RequestException as e:
            print(f"ESP32 통신 오류: {e}")
            return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류가 발생했습니다: {e}"}, status=500)
    else:
        return JsonResponse({
            'status': 'fail',
            'message': 'POST 요청만 허용됩니다.'
        }, status=400)


@csrf_exempt
def aircon_power_off(request):
    if request.method == "POST":
        controller_id = request.POST.get("controller_id")
        print(f"controller_id: {controller_id}")
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"}, status=404) # status 추가
        
        # ▼ 이 부분 수정: 'power_off'에 해당하는 코드 가져오기 ▼
        try:
            code = aircon.loc[aircon.motion == 'power_off', 'code'].iloc[0] # 'motion' 컬럼이 'power_off'인 행의 'code'
        except IndexError: # 해당하는 motion이 없을 경우
            return JsonResponse({'status': 'fail', 'message': "에어컨 전원 끄기 코드를 찾을 수 없습니다."}, status=404)

        url = f"http://{controller.ip_address}/ir?code={code}" # 동적으로 가져온 코드 사용
        print(f"ESP32로 전송될 URL (Power OFF): {url}") # 디버깅을 위한 로그
        
        try:
            res = requests.get(url, timeout=5) # timeout 추가
            res.raise_for_status()
            print(f"ESP32 응답: {res.text}")
            return JsonResponse({
                'status': 'success',
                'message': '에어컨 전원이 꺼졌습니다!'
            })
        except requests.exceptions.RequestException as e:
            print(f"ESP32 통신 오류: {e}")
            return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류가 발생했습니다: {e}"}, status=500)
    else:
        return JsonResponse({
            'status': 'fail',
            'message': 'POST 요청만 허용됩니다.'
        }, status=400)
    

@csrf_exempt
def aircon_set_temp(request):
    if request.method == "POST":
        # 여기에 실제 동작 넣기 (ex. 기기 제어 코드)
        controller_id = request.POST.get("controller_id")
        temperature = request.POST.get("temperature")
        print(f"controller_id: {controller_id}")
        print(f"temperature : {temperature}")

        if not controller_id:
            return JsonResponse({'status': 'fail', 'message': "컨트롤러 ID가 전송되지 않았습니다."}, status=400)
        if not temperature or not temperature.isdigit():
            return JsonResponse({'status': 'fail', 'message': "유효한 온도 값이 전송되지 않았습니다. (숫자만 가능)"}, status=400)
        
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"})
        
        code = aircon.loc[
            (aircon.motion.str.contains(temperature)) &
            (aircon.bits == 24), 'code']
        url = f"http://{controller.ip_address}/ir?code={code}"
        print(f"url : {url}")
        res = requests.get(url)
        print(f"{res.text}")

        return JsonResponse({
            'status': 'success',
            'message': f'에어컨 온도가 {temperature}°C로 설정되었습니다.'
        })
    else:
        return JsonResponse({
            'status': 'fail',
            'message': 'POST 요청만 허용됩니다.'
        }, status=400)
    

@csrf_exempt
def aircon_mode_auto(request):
    if request.method == "POST":
        controller_id = request.POST.get("controller_id")
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"}, status=404)
        
        try:
            code = aircon.loc[aircon['motion'] == 'mode_cool', 'code'].iloc[0]
        except IndexError:
            return JsonResponse({'status': 'fail', 'message': "냉방 모드 코드를 찾을 수 없습니다."}, status=404)
        
        url = f"http://{controller.ip_address}/ir?code={code}" 
        print(f"ESP32로 전송될 URL (Cool Mode): {url}")
        
        try:
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            print(f"ESP32 응답: {res.text}")
            return JsonResponse({'status': 'success', 'message': '에어컨 냉방 모드로 설정되었습니다!'})
        except requests.exceptions.RequestException as e:
            print(f"ESP32 통신 오류: {e}")
            return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류가 발생했습니다: {e}"}, status=500)
    return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)


@csrf_exempt
def aircon_mode_cool(request):
    if request.method == "POST":
        controller_id = request.POST.get("controller_id")
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"}, status=404)
        
        try:
            code = aircon.loc[aircon['motion'] == 'mode_cool', 'code'].iloc[0]
        except IndexError:
            return JsonResponse({'status': 'fail', 'message': "냉방 모드 코드를 찾을 수 없습니다."}, status=404)
        
        url = f"http://{controller.ip_address}/ir?code={code}" 
        print(f"ESP32로 전송될 URL (Cool Mode): {url}")
        
        try:
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            print(f"ESP32 응답: {res.text}")
            return JsonResponse({'status': 'success', 'message': '에어컨 냉방 모드로 설정되었습니다!'})
        except requests.exceptions.RequestException as e:
            print(f"ESP32 통신 오류: {e}")
            return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류가 발생했습니다: {e}"}, status=500)
    return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)


@csrf_exempt
def aircon_mode_fan(request):
    if request.method == "POST":
        controller_id = request.POST.get("controller_id")
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"}, status=404)
        
        try:
            code = aircon.loc[aircon['motion'] == 'mode_fan', 'code'].iloc[0]
        except IndexError:
            return JsonResponse({'status': 'fail', 'message': "송풍 모드 코드를 찾을 수 없습니다."}, status=404)
        
        url = f"http://{controller.ip_address}/ir?code={code}" 
        print(f"ESP32로 전송될 URL (Fan Mode): {url}")
        
        try:
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            print(f"ESP32 응답: {res.text}")
            return JsonResponse({'status': 'success', 'message': '에어컨 송풍 모드로 설정되었습니다!'})
        except requests.exceptions.RequestException as e:
            print(f"ESP32 통신 오류: {e}")
            return JsonResponse({'status': 'fail', 'message': f"기기 통신 오류가 발생했습니다: {e}"}, status=500)
    return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)
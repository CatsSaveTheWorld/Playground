from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from ..models import Controller  # 모델 import
import pandas as pd
import requests


wifi_path = r"E:\Python\github\PLAYGROUND\smartcore\management\data\wifi.csv".replace('\\', '/')
aircon_path = r"E:\Python\github\PLAYGROUND\smartcore\management\data\aircon_control_code.csv".replace('\\', '/')


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
        controller_id = request.POST.get('controller_id')
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"})
        
        url = f"http://{controller.ip_address}?coode=0xB2BFD0"
        res = requests.get(url)
        print(f"{res.text}")

        return JsonResponse({
            'status': 'success',
            'message': '에어컨 전원이 켜졌습니다!'
        })
    else:
        return JsonResponse({
            'status': 'fail',
            'message': 'POST 요청만 허용됩니다.'
        }, status=400)


@csrf_exempt
def aircon_power_off(request):
    if request.method == "POST":
        # 여기에 실제 동작 넣기 (ex. 기기 제어 코드)
        controller_id = request.POST.get('controller_id')
        try:
            controller = Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message' : "컨트롤러 없음"})
        
        url = f"http://{controller.ip_address}?coode=0xB27BE0"
        res = requests.get(url)
        print(f"{res.text}")

        return JsonResponse({
            'status': 'success',
            'message': '에어컨 전원이 꺼졌습니다!'
        })
    else:
        return JsonResponse({
            'status': 'fail',
            'message': 'POST 요청만 허용됩니다.'
        }, status=400)
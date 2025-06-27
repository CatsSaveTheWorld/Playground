from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
# from ..forms import QuestionForm
# from ..models import Question

# Create your views here.
def detail_list(request):
    return render(request, "smartcore/detail_list.html")



@csrf_exempt
def aircon_power_on(request):
    if request.method == "POST":
        # 여기에 실제 동작 넣기 (ex. 기기 제어 코드)
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
        return JsonResponse({
            'status': 'success',
            'message': '에어컨 전원이 꺼졌습니다!'
        })
    else:
        return JsonResponse({
            'status': 'fail',
            'message': 'POST 요청만 허용됩니다.'
        }, status=400)
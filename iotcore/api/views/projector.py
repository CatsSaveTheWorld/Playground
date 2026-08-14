from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ...device.services.device_service import DeviceService
from ...models import Controller, Device
from .common import parse_request_data


PROJECTOR_ACTIONS = {
    "power",
    "external_input",
    "home",
    "menu",
    "back",
    "up",
    "down",
    "left",
    "right",
    "ok",
    "volume_down",
    "mute",
    "volume_up",
}

ACTION_MESSAGES = {
    "power": "프로젝터 전원 신호를 전송했습니다.",
    "external_input": "External Input 신호를 전송했습니다.",
    "home": "홈 신호를 전송했습니다.",
    "menu": "메뉴 신호를 전송했습니다.",
    "back": "뒤로 가기 신호를 전송했습니다.",
    "up": "위 방향 신호를 전송했습니다.",
    "down": "아래 방향 신호를 전송했습니다.",
    "left": "왼쪽 방향 신호를 전송했습니다.",
    "right": "오른쪽 방향 신호를 전송했습니다.",
    "ok": "확인 신호를 전송했습니다.",
    "volume_down": "음량 내리기 신호를 전송했습니다.",
    "mute": "음소거 신호를 전송했습니다.",
    "volume_up": "음량 올리기 신호를 전송했습니다.",
}


@login_required(login_url="common:login")
def projector_control(request, device_id):
    device = get_object_or_404(
        Device,
        id=device_id,
        device_type="projector",
    )
    controller = (
        Controller.objects
        .filter(device=device)
        .first()
    )

    return render(
        request,
        "iotcore/projector_control.html",
        {
            "device": device,
            "controller": controller,
        },
    )


@login_required(login_url="common:login")
@require_POST
def projector_action(request, device_id):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "POST 요청만 허용됩니다."},
            status=405,
        )

    device = Device.objects.filter(
        id=device_id,
        device_type="projector",
    ).first()
    if not device:
        return JsonResponse(
            {"success": False, "message": "프로젝터를 찾을 수 없습니다."},
            status=404,
        )

    data = parse_request_data(request)
    motion = data.get("motion") or data.get("function")

    if motion not in PROJECTOR_ACTIONS:
        return JsonResponse(
            {
                "success": False,
                "message": f"지원하지 않는 프로젝터 동작입니다. ({motion})",
            },
            status=400,
        )

    success, message = DeviceService.control(
        device_id=device.id,
        motion=motion,
        success_message=ACTION_MESSAGES.get(motion),
    )

    return JsonResponse(
        {"success": success, "message": message},
        status=200 if success else 400,
    )

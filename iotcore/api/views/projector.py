from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ...device.services.device_service import DeviceService
from ...device.services.device_state_service import DeviceStateService
from ...models import Controller, Device
from .common import parse_request_data


PROJECTOR_ACTIONS = {
    # ``power`` is kept for backward compatibility with older saved Actions.
    "power",
    "power_on",
    "power_off",
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
    "power": "프로젝터 전원 토글 신호를 전송했습니다.",
    "power_on": "프로젝터 전원 켜기 동작을 처리했습니다.",
    "power_off": "프로젝터 전원 끄기 동작을 처리했습니다.",
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


def _power_state_context(device):
    value = DeviceStateService.get_value(device, "power")
    if value is True:
        return value, "on", "켜짐"
    if value is False:
        return value, "off", "꺼짐"
    return None, "unknown", "알 수 없음"


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
    power_state, power_state_code, power_state_label = _power_state_context(device)

    return render(
        request,
        "iotcore/projector_control.html",
        {
            "device": device,
            "controller": controller,
            "power_state": power_state,
            "power_state_code": power_state_code,
            "power_state_label": power_state_label,
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

    power_state, _code, _label = _power_state_context(device)
    return JsonResponse(
        {
            "success": success,
            "message": message,
            "power_state": power_state,
        },
        status=200 if success else 400,
    )


@login_required(login_url="common:login")
@require_POST
def projector_power_state_sync(request, device_id):
    """Synchronize IoTCore's tracked projector power state without sending IR.

    IR is one-way, so a freshly initialized projector starts as unknown.  This
    endpoint lets the operator establish the one-time ON/OFF baseline from the
    actual projector.  Subsequent logical power_on/power_off operations and
    legacy raw power toggles keep the state updated automatically.
    """
    device = get_object_or_404(
        Device,
        id=device_id,
        device_type="projector",
    )
    data = parse_request_data(request)
    raw_value = data.get("power")
    if raw_value is None:
        raw_value = data.get("state")

    normalized = str(raw_value).strip().lower()
    if normalized in {"true", "1", "on", "yes"}:
        power = True
    elif normalized in {"false", "0", "off", "no"}:
        power = False
    else:
        return JsonResponse(
            {
                "success": False,
                "message": "전원 상태는 on/off 또는 true/false로 지정해야 합니다.",
            },
            status=400,
        )

    # Synchronization establishes a baseline only; it intentionally sends no IR
    # command and does not overwrite controller_last_command.
    DeviceStateService.set_state(device, "power", power)

    return JsonResponse(
        {
            "success": True,
            "message": (
                "프로젝터 실제 상태를 '켜짐'으로 동기화했습니다."
                if power
                else "프로젝터 실제 상태를 '꺼짐'으로 동기화했습니다."
            ),
            "power_state": power,
        }
    )

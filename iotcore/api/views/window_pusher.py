from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from ...device.repositories.device_repository import DeviceRepository
from ...device.services.device_service import DeviceService
from ...device.services.window_pusher_service import WindowPusherService
from ...models import Device
from .common import parse_request_data


ALLOWED_ACTIONS = {"move_left", "stop", "move_right", "set_position"}


def _error(message, *, status=400):
    return JsonResponse({"success": False, "message": message}, status=status)


def _window_pusher(device_id):
    device = DeviceRepository.get_by_id(device_id)
    if device is None:
        return None, _error("창문 푸셔를 찾을 수 없습니다.", status=404)
    if device.device_type != WindowPusherService.DEVICE_TYPE:
        return None, _error("창문 푸셔로 등록된 기기만 제어할 수 있습니다.")
    if device.device_role not in {Device.Role.CONTROL, Device.Role.HYBRID}:
        return None, _error("제어 가능한 창문 푸셔가 아닙니다.")
    if device.protocol != Device.Protocol.ZIGBEE:
        return None, _error("Zigbee 프로토콜로 등록된 창문 푸셔만 제어할 수 있습니다.")
    return device, None


@login_required(login_url="common:login")
@require_POST
def window_pusher_control(request, device_id):
    device, error = _window_pusher(device_id)
    if error is not None:
        return error

    data = parse_request_data(request)
    action = str(data.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        return _error("지원하지 않는 창문 푸셔 동작입니다.")

    position = None
    if action == "set_position":
        try:
            position = WindowPusherService.parse_position(data.get("position"))
        except ValueError as exc:
            return _error(str(exc))

    success, message = DeviceService.control(
        device.id,
        action,
        window_position=position,
    )
    return JsonResponse(
        {
            "success": success,
            "message": message,
            "state": WindowPusherService.snapshot(device),
        },
        status=200 if success else 400,
    )


@login_required(login_url="common:login")
@require_GET
def window_pusher_state(request, device_id):
    device, error = _window_pusher(device_id)
    if error is not None:
        return error
    return JsonResponse({"success": True, "state": WindowPusherService.snapshot(device)})

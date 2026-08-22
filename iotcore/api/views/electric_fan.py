from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from ...device.repositories.device_repository import DeviceRepository
from ...device.services.device_service import DeviceService
from ...models import Device
from .common import parse_request_data


VALUELESS_ACTIONS = {
    "power_on",
    "power_off",
    "vertical_swing_on",
    "vertical_swing_off",
    "horizontal_swing_on",
    "horizontal_swing_off",
    "beep_on",
    "beep_off",
}
ALLOWED_ACTIONS = VALUELESS_ACTIONS | {
    "set_speed",
    "set_horizontal_angle",
}


def _validation_error(message):
    return JsonResponse({"success": False, "message": message}, status=400)


def _parse_speed(value):
    if isinstance(value, bool):
        raise ValueError("1부터 100 사이의 정수를 입력하세요.")
    if isinstance(value, int):
        speed = value
    elif isinstance(value, str) and value.strip().isdigit():
        speed = int(value.strip())
    else:
        raise ValueError("1부터 100 사이의 정수를 입력하세요.")
    if not 1 <= speed <= 100:
        raise ValueError("풍속은 1부터 100 사이여야 합니다.")
    return speed


def _parse_device_id(value):
    if isinstance(value, bool):
        raise ValueError("올바른 device_id를 입력하세요.")
    if isinstance(value, int):
        device_id = value
    elif isinstance(value, str) and value.strip().isdigit():
        device_id = int(value.strip())
    else:
        raise ValueError("올바른 device_id를 입력하세요.")
    if device_id < 1:
        raise ValueError("올바른 device_id를 입력하세요.")
    return device_id


def _validated_fan_value(action, raw_value):
    if action in VALUELESS_ACTIONS:
        return None
    if raw_value is None or raw_value == "":
        raise ValueError("동작에 필요한 설정값이 없습니다.")
    if action == "set_speed":
        return _parse_speed(raw_value)
    if action == "set_horizontal_angle":
        if not isinstance(raw_value, str) or raw_value.strip() not in {"30", "60", "90"}:
            raise ValueError("좌우 회전 각도는 30, 60 또는 90만 사용할 수 있습니다.")
        return raw_value.strip()
    raise ValueError("지원하지 않는 선풍기 동작입니다.")


@login_required(login_url="common:login")
@require_POST
def electricfan_control(request):
    """Validate a web command and delegate it to the shared device service."""
    data = parse_request_data(request)
    if not isinstance(data, dict):
        return _validation_error("올바른 요청 형식이 아닙니다.")

    raw_device_id = data.get("device_id")
    action = str(data.get("action") or "").strip()

    if not raw_device_id:
        return _validation_error("device_id가 없습니다.")
    if action not in ALLOWED_ACTIONS:
        return _validation_error("지원하지 않는 선풍기 동작입니다.")

    try:
        device_id = _parse_device_id(raw_device_id)
    except ValueError as exc:
        return _validation_error(str(exc))

    device = DeviceRepository.get_by_id(device_id)
    if not device:
        return JsonResponse(
            {"success": False, "message": f"존재하지 않는 device_id: {device_id}"},
            status=404,
        )
    if device.device_type != "electric_fan":
        return _validation_error("선풍기로 등록된 기기만 제어할 수 있습니다.")
    if device.device_role not in {Device.Role.CONTROL, Device.Role.HYBRID}:
        return _validation_error("제어 기기로 등록된 선풍기만 제어할 수 있습니다.")
    if device.protocol != Device.Protocol.TUYA:
        return _validation_error("Tuya 프로토콜로 등록된 선풍기만 제어할 수 있습니다.")

    try:
        fan_value = _validated_fan_value(action, data.get("fan_value"))
    except ValueError as exc:
        return _validation_error(str(exc))

    success, message = DeviceService.control(
        device.id,
        action,
        fan_value=fan_value,
    )
    return JsonResponse(
        {"success": success, "message": message},
        status=200 if success else 400,
    )

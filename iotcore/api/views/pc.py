from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from ...device.repositories.device_repository import DeviceRepository
from ...device.services.device_service import DeviceService
from ...models import Device
from .common import parse_request_data


ALLOWED_ACTIONS = {"power_on", "power_off"}


def _validation_error(message, status=400):
    return JsonResponse({"success": False, "status": "fail", "message": message}, status=status)


def _resolve_pc(data):
    """Resolve a PC from the Device table only.

    ``device_id`` is canonical. ``pc_name`` remains only as a compatibility
    lookup for old callers. Client-supplied MAC/IP values are never trusted.
    """
    raw_device_id = data.get("device_id")
    if raw_device_id not in (None, ""):
        try:
            device_id = int(raw_device_id)
        except (TypeError, ValueError):
            return None, "올바른 device_id를 입력하세요.", 400
        device = DeviceRepository.get_by_id(device_id)
    else:
        pc_name = str(data.get("pc_name") or "").strip()
        device = (
            Device.objects.filter(device_type="pc", name=pc_name).first()
            if pc_name
            else None
        )

    if device is None:
        return None, "등록된 PC Device를 찾을 수 없습니다.", 404
    if device.device_type != "pc":
        return None, "PC로 등록된 Device만 제어할 수 있습니다.", 400
    if device.device_role not in {Device.Role.CONTROL, Device.Role.HYBRID}:
        return None, "제어 가능한 PC Device가 아닙니다.", 400
    return device, None, 200


def _handle_pc_control(request, *, forced_action=None):
    data = parse_request_data(request)
    if not isinstance(data, dict):
        return _validation_error("올바른 요청 형식이 아닙니다.")

    action = str(
        forced_action or data.get("action") or data.get("motion") or ""
    ).strip()
    if action not in ALLOWED_ACTIONS:
        return _validation_error("지원하지 않는 PC 동작입니다.")

    device, error, status = _resolve_pc(data)
    if error:
        return _validation_error(error, status=status)

    success, message = DeviceService.control(device.id, action)
    return JsonResponse(
        {
            "success": success,
            "status": "success" if success else "fail",
            "message": message,
        },
        status=200 if success else 400,
    )


@login_required(login_url="common:login")
@require_POST
def pc_control(request):
    return _handle_pc_control(request)


@login_required(login_url="common:login")
@require_POST
def pc_power_on(request):
    return _handle_pc_control(request, forced_action="power_on")


@login_required(login_url="common:login")
@require_POST
def pc_power_off(request):
    return _handle_pc_control(request, forced_action="power_off")

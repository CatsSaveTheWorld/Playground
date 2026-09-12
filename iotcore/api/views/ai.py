from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ...ai.service import AIControlService
from .common import parse_request_data


@csrf_exempt
def ai_control(request):
    """Optional AI entry point using the same IoTCore service layer as the UI.

    Direct action:
      {device_uid | (device_type + location), function, parameter?}

    Existing Automation:
      {automation_id}
    """
    if request.method != "POST":
        return JsonResponse(
            {"status": "fail", "message": "POST 요청만 허용됩니다."},
            status=400,
        )

    data = parse_request_data(request)
    try:
        automation_id = data.get("automation_id")
        if automation_id not in (None, ""):
            run = AIControlService.run_automation(automation_id)
            return JsonResponse({
                "status": "success",
                "run_id": run.pk,
                "message": "자동화 실행 요청을 등록했습니다.",
            })

        device = AIControlService.resolve_device(
            device_uid=data.get("device_uid"),
            device_type=data.get("device_type") or data.get("device"),
            location=data.get("location"),
        )
        function = str(data.get("function") or "").strip()
        parameter = data.get("parameter") or data.get("parameters") or {}
        if device is None:
            return JsonResponse(
                {"status": "fail", "message": "대상 기기를 찾을 수 없습니다."},
                status=404,
            )
        if not function:
            return JsonResponse(
                {"status": "fail", "message": "function이 필요합니다."},
                status=400,
            )
        if not isinstance(parameter, dict):
            return JsonResponse(
                {"status": "fail", "message": "parameter는 JSON object여야 합니다."},
                status=400,
            )

        success, message = AIControlService.execute_device_action(
            device=device,
            function=function,
            parameter=parameter,
        )
        return JsonResponse(
            {"status": "success" if success else "fail", "message": message},
            status=200 if success else 400,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse({"status": "fail", "message": message}, status=400)

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from ...ai.routing import assign_role, snapshot, probe_device, RoutingConflict
from ...models import Device
from .common import parse_request_data


@login_required(login_url='common:login')
@require_GET
@never_cache
def routing_state(request):
    data = {'success': True, 'routes': snapshot()}
    if request.GET.get('probe_device'):
        device = Device.objects.filter(pk=request.GET['probe_device'], device_type='pc', device_role__in=['control', 'hybrid']).first() if request.GET['probe_device'].isdigit() else None
        if device is None:
            return JsonResponse({'success': False, 'message': 'PC를 찾을 수 없습니다.'}, status=400)
        data['health'] = {str(device.pk): probe_device(device)}
    return JsonResponse(data)


@login_required(login_url='common:login')
@require_POST
def routing_assign(request):
    if not request.user.is_staff:
        return JsonResponse({'success': False, 'message': '관리자만 AI 배치를 변경할 수 있습니다.'}, status=403)
    data = parse_request_data(request)
    if not isinstance(data, dict):
        return JsonResponse({'success': False, 'message': '잘못된 요청입니다.'}, status=400)
    try:
        assign_role(str(data.get('service', '')), int(data.get('device_id', '')), str(data.get('role', '')), int(data.get('revision', '')))
    except (ValueError, TypeError, ValidationError) as exc:
        return JsonResponse({'success': False, 'message': '; '.join(exc.messages) if isinstance(exc, ValidationError) else '설정 값이 올바르지 않습니다.'}, status=400)
    except RoutingConflict as exc:
        return JsonResponse({'success': False, 'message': str(exc), 'routes': snapshot()}, status=409)
    return JsonResponse({'success': True, 'message': 'AI 배치를 저장했습니다. 다음 요청부터 적용됩니다.', 'routes': snapshot()})

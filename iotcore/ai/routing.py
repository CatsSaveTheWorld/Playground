"""PC selection, bounded inference failover, and recent routing outcomes."""
from dataclasses import dataclass, replace
from ipaddress import ip_address

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
import requests

from ..models import AIServiceRoute, Device

SERVICES = ('asr', 'nlp', 'tts')
PORTS = {'asr': 6101, 'nlp': 6201}


@dataclass(frozen=True)
class Target:
    url: str
    node_uid: str
    role: str
    error: str = ''


def worker_url(service, device):
    if device is None:
        raise ValueError('PC가 지정되지 않았습니다.')
    if device.device_type != 'pc' or not device.is_controllable:
        raise ValueError('제어 가능한 PC만 선택할 수 있습니다.')
    config = device.control_config or {}
    host = ip_address(str(config.get('ip_address') or '').strip())
    if host.is_unspecified or host.is_multicast:
        raise ValueError('유효한 PC IP 주소가 필요합니다.')
    configured = (config.get('ai_services') or {}).get(service) or {}
    port = configured.get('port', PORTS.get(service))
    if port is None:
        raise ValueError('TTS 연동 준비 중')
    if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('워커 포트가 올바르지 않습니다.')
    address = f'[{host}]' if host.version == 6 else str(host)
    return f'http://{address}:{int(port)}'


def targets(service, legacy_url):
    try:
        route = AIServiceRoute.objects.select_related('active_device', 'standby_device').filter(pk=service).first()
    except DatabaseError:
        return None, [Target('', '', 'active', 'AI 라우팅 설정을 읽을 수 없습니다.')]
    if route is None or not route.configured:
        return None, [Target(legacy_url, getattr(settings, 'IOTCORE_AI_NODE_UID', 'home-ai-main'), 'active')]
    choices = [('active', route.active_device)]
    if route.failover_enabled and route.standby_device_id:
        choices.append(('standby', route.standby_device))
    result = []
    for role, device in choices:
        try:
            url, error = worker_url(service, device), ''
        except (ValueError, TypeError, AttributeError) as exc:
            url, error = '', str(exc)
        result.append(Target(url, device.device_uid if device else '', role, error))
    return route, result


def dispatch(client, service, invoke, make_failure):
    """Retry only unavailable inference workers, never rejected device commands."""
    from copy import copy
    route, candidates = targets(service, client.base_url)
    attempts = []
    for target in candidates:
        if target.error or not target.url:
            result = make_failure(target.error or '워커 주소가 비어 있습니다.')
        else:
            attempt = copy(client)
            attempt.base_url = target.url
            attempt.node_uid = target.node_uid
            attempt.route_role = target.role
            result = invoke(attempt)
        attempts.append({'node_uid': target.node_uid, 'role': target.role, 'ok': result.ok,
                         'unavailable': result.unavailable, 'error': result.error[:300]})
        result = replace(result, node_uid=target.node_uid, used_standby=target.role == 'standby', attempts=list(attempts))
        if result.ok or not result.unavailable:
            break
    if route is not None:
        outcome = {'status': ('standby' if result.used_standby else 'active') if result.ok else ('unavailable' if result.unavailable else 'rejected'),
                   'node_uid': result.node_uid, 'at': timezone.now().isoformat(), 'attempts': attempts}
        try:
            AIServiceRoute.objects.filter(pk=service, revision=route.revision).update(last_result=outcome)
        except DatabaseError:
            pass  # A monitoring write cannot turn successful inference into a retry.
    return result


class RoutingConflict(Exception):
    pass


def assign_role(service, device_id, role, revision):
    if service not in SERVICES or role not in ('active', 'standby', 'off'):
        raise ValidationError('모델 또는 역할이 올바르지 않습니다.')
    device = Device.objects.filter(pk=device_id, device_type='pc', device_role__in=['control', 'hybrid']).first()
    if device is None:
        raise ValidationError('제어 가능한 PC를 찾을 수 없습니다.')
    # TTS currently has no inference contract, but its PC assignment can be saved.
    try:
        worker_url('asr' if service == 'tts' else service, device)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('PC 주소 설정을 확인하세요: ' + str(exc))
    with transaction.atomic():
        route, created = AIServiceRoute.objects.get_or_create(service=service)
        if route.revision != revision:
            raise RoutingConflict('다른 화면에서 설정이 변경됐습니다. 최신 설정을 다시 확인하세요.')
        active, standby = route.active_device_id, route.standby_device_id
        if role == 'active':
            if active != device.pk:
                active, standby = device.pk, active
        elif role == 'standby':
            if active == device.pk:
                if standby is None:
                    raise ValidationError('다른 액티브 PC를 먼저 선택하세요.')
                active, standby = standby, active
            else:
                standby = device.pk
        else:
            if active == device.pk:
                if standby is None:
                    raise ValidationError('액티브 PC는 최소 한 대가 필요합니다.')
                active, standby = standby, None
            elif standby == device.pk:
                standby = None
        if active is None:
            raise ValidationError('액티브 PC를 먼저 선택하세요.')
        changed = AIServiceRoute.objects.filter(pk=service, revision=revision).update(
            active_device_id=active, standby_device_id=standby, configured=True,
            revision=revision+1, updated_at=timezone.now(), last_result={})
        if not changed:
            raise RoutingConflict('설정이 변경됐습니다. 최신 설정을 다시 확인하세요.')


def assign_role_for_automation(service, device_id, role):
    """Read the current revision at execution time, then use the UI role rules."""
    if service not in SERVICES or role not in ('active', 'standby', 'off'):
        raise ValidationError('모델 또는 역할이 올바르지 않습니다.')
    if not Device.objects.filter(pk=device_id, device_type='pc', device_role__in=['control', 'hybrid']).exists():
        raise ValidationError('제어 가능한 PC를 찾을 수 없습니다.')
    with transaction.atomic():
        route = AIServiceRoute.objects.select_for_update().filter(pk=service).first()
        if route is None:
            if role == 'off':
                return
            AIServiceRoute.objects.get_or_create(service=service)
            route = AIServiceRoute.objects.select_for_update().get(pk=service)
        if role == 'off' and device_id not in (route.active_device_id, route.standby_device_id):
            return
        assign_role(service, device_id, role, route.revision)


def snapshot():
    routes = {route.service: route for route in AIServiceRoute.objects.select_related('active_device', 'standby_device')}
    result = {}
    for service in SERVICES:
        route = routes.get(service)
        def describe(device):
            if device is None:
                return None
            try:
                url, error = worker_url(service, device), ''
            except (ValueError, TypeError, AttributeError) as exc:
                url, error = '', str(exc)
            return {'id': device.pk, 'name': device.name, 'node_uid': device.device_uid, 'url': url, 'error': error}
        result[service] = {'service': service, 'configured': bool(route and route.configured),
            'revision': route.revision if route else 1,
            'active': describe(route.active_device) if route else None,
            'standby': describe(route.standby_device) if route else None,
            'failover_enabled': route.failover_enabled if route else True,
            'pending': service == 'tts', 'last_result': route.last_result if route else {}}
    return result


def probe_device(device):
    result = {}
    for service in SERVICES:
        if service == 'tts':
            result[service] = {'status': 'pending', 'message': 'TTS 연동 준비 중'}
            continue
        try:
            url = worker_url(service, device)
            key = f'ai-worker-health:{device.pk}:{url}'
            health = cache.get(key)
            if health is None:
                response = requests.get(url+'/health', timeout=(0.8, 1.2), allow_redirects=False)
                payload = response.json()
                expected = f'iotcore-{service}-worker'
                healthy = response.status_code == 200 and isinstance(payload, dict) and payload.get('ok') is True and payload.get('service') == expected
                health = {'status': 'ready' if healthy else 'unavailable', 'message': '요청 가능' if healthy else '워커 준비 안 됨'}
                cache.set(key, health, 5)
            result[service] = health
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            result[service] = {'status': 'unavailable', 'message': '워커 연결 실패'}
    return result

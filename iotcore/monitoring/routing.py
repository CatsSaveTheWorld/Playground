"""Read the same routing configuration as inference, without contacting workers."""
from urllib.parse import urlsplit

from django.conf import settings

from ..ai.routing import snapshot
from ..models import Device
from .inference import AIInferenceMonitor
from .service import NodeTelemetryService


def routing_summary():
    routes = snapshot()
    pcs = list(Device.objects.filter(device_type='pc'))

    def describe(target):
        if not target:
            return {'name': '미지정', 'address': ''}
        return {'name': target['name'], 'address': urlsplit(target.get('url') or '').netloc or '주소 확인 필요'}

    current_nodes = {}
    rows = []
    for service, route in routes.items():
        active_uid = (route['active'] or {}).get('node_uid')
        active, standby = describe(route['active']), describe(route['standby'])
        note = '액티브 우선 · 장애 시 스탠바이' if route['standby'] and route['failover_enabled'] else '스탠바이 미지정'
        if route['standby'] and not route['failover_enabled']:
            note = '자동 전환 꺼짐'
        state = 'configured' if route['active'] else 'unassigned'
        if service == 'tts':
            state, note = 'pending', '연동 준비 중 · 배치만 저장'
            # TTS has no worker contract; a saved selection is still meaningful.
            active['address'] = standby['address'] = ''
        else:
            if not route['configured']:
                url = str(getattr(settings, f'VOICE_{service.upper()}_BASE_URL', '')).strip().rstrip('/')
                parsed = urlsplit(url)
                host = parsed.hostname or ''
                pc = next((pc for pc in pcs if str((pc.control_config or {}).get('ip_address', '')).strip() == host), None)
                active_uid = pc.device_uid if pc else getattr(settings, 'IOTCORE_AI_NODE_UID', 'home-ai-main')
                active = {'name': pc.name if pc else host or '미지정', 'address': parsed.netloc}
                standby = describe(None)
                state, note = ('legacy', '기본 연결 설정 · 스탠바이 없음') if host else ('unassigned', '워커 주소 미설정')
            elif route['active'] and route['active']['error']:
                state, note = 'unassigned', '액티브 PC 주소 확인 필요'
            elif not route['active']:
                note = '액티브 PC 미지정'
            if not getattr(settings, f'VOICE_{service.upper()}_ENABLED', False):
                state, note = 'disabled', '요청 비활성'

        current = {}
        if service != 'tts' and active_uid and state not in ('disabled', 'unassigned'):
            if active_uid not in current_nodes:
                current_nodes[active_uid] = AIInferenceMonitor.snapshot(NodeTelemetryService.snapshot(active_uid))
            current = current_nodes[active_uid]['services'].get(service) or {}
        status = 'pending' if service == 'tts' else 'disabled' if state == 'disabled' else current.get('status', 'unknown')
        label = '연동 준비 중' if service == 'tts' else '비활성' if state == 'disabled' else current.get('label', '확인 불가')
        count = current.get('active_requests', 0)
        usage = f'처리 중 {count}건' if status in ('ready', 'processing') else '현재 처리량 확인 대기'
        if status in ('pending', 'disabled'):
            usage = ''
        rows.append({'service': service, 'label': service.upper(), 'state': state,
                     'active': active, 'standby': standby, 'note': note,
                     'status': status, 'status_label': label,
                     'model': ' · '.join(filter(None, [current.get('engine') or current.get('model'), current.get('device')])),
                     'usage': usage, 'error': current.get('error', '') if not current.get('stale') else '',
                     'expires_in_seconds': 5 if status == 'processing' else current.get('expires_in_seconds', 0)})
    return rows

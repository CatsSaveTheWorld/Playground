"""Read-only, bounded projections of recorded executions. Never query live state."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.core.paginator import Paginator
from django.db.models import Case, CharField, Exists, OuterRef, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .device_actions import DeviceActionRegistry
from .models import Action, ActionRun, AutomationNodeRun, AutomationRun

KST = ZoneInfo('Asia/Seoul')
LABELS = {'pending': '대기', 'waiting': '지연 대기', 'running': '실행 중', 'success': '완료', 'failed': '실패', 'cancelled': '취소', 'skipped': '동작 없음', 'no_action': '동작 없음'}


class HistoryFilter(forms.Form):
    start = forms.DateField(required=False, label='시작일', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='종료일', widget=forms.DateInput(attrs={'type': 'date'}))
    status = forms.ChoiceField(required=False, label='결과', choices=[('', '전체')] + [(key, LABELS[key]) for key in ['success', 'failed', 'cancelled', 'no_action', 'running', 'pending', 'waiting']])

    def clean(self):
        data = super().clean()
        for key in ['start', 'end']:
            if data.get(key) and not 1900 <= data[key].year <= 9998:
                raise forms.ValidationError('조회 날짜는 1900년부터 9998년 사이로 입력하세요.')
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise forms.ValidationError('종료일은 시작일 이후여야 합니다.')
        return data


def action_label(function, device_type=''):
    for action in DeviceActionRegistry._ACTIONS.get(device_type, []):
        if action.code == function:
            return action.display_name
    if str(function).startswith('set_temp_'):
        return '온도 설정 ' + str(function)[9:] + '°C'
    return function or '기기 명령'


def run_name(run):
    name = run.automation_name or '삭제된 자동화'
    if name.startswith('__window_pusher_safety_stop__'):
        return '창문 안전 정지'
    return name


def duration(start, end):
    if start is None or end is None:
        return '—'
    seconds = max(0, (end - start).total_seconds())
    if seconds < 60:
        return f'{seconds:.1f}초'
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}시간 {minutes}분 {seconds}초' if hours else f'{minutes}분 {seconds}초'


def root_runs():
    commands = ActionRun.objects.filter(root_run_id=OuterRef('pk'), action_type=Action.Type.DEVICE)
    return AutomationRun.objects.filter(root_run__isnull=True, parent_action_run__isnull=True).select_related('automation').annotate(
        history_at=Coalesce('started_at', 'created_at'),
        has_commands=Exists(commands),
        history_status=Case(
            When(Q(status=AutomationRun.Status.SKIPPED) | (Q(status=AutomationRun.Status.SUCCESS) & ~Exists(commands)), then=Value('no_action')),
            default='status', output_field=CharField(),
        ),
        history_kind=Case(When(trigger_payload__kind='device_control', then=Value('기기 제어')), default=Value('자동화'), output_field=CharField()),
    )


def history_context(request):
    data = request.GET.copy()
    if not request.GET:
        today = timezone.localtime(timezone.now(), KST).date().isoformat()
        data.update({'start': today, 'end': today})
    form = HistoryFilter(data)
    runs = root_runs().defer('graph_snapshot', 'trigger_payload')
    if form.is_valid():
        cleaned = form.cleaned_data
        if cleaned.get('start'):
            runs = runs.filter(history_at__gte=datetime.combine(cleaned['start'], time.min, KST))
        if cleaned.get('end'):
            runs = runs.filter(history_at__lt=datetime.combine(cleaned['end'] + timedelta(days=1), time.min, KST))
        if cleaned.get('status'):
            runs = runs.filter(history_status=cleaned['status'])
    else:
        runs = runs.none()
    page = Paginator(runs.order_by('-history_at', '-pk'), 50).get_page(request.GET.get('page'))
    for run in page:
        run.history_name = run_name(run)
        run.history_label = LABELS.get(run.history_status, run.get_status_display())
        run.history_duration = duration(run.started_at, run.finished_at)
        if run.history_kind != '기기 제어':
            run.history_kind = '예약 실행' if run.automation and run.automation.automation_type == 'scheduled' else '시퀀스'
    params = data.copy()
    params.pop('page', None)
    return {'history_filter': form, 'history_page': page, 'history_query': params.urlencode()}


def execution_detail_context(root):
    # A hard cap prevents one pathological execution tree from exhausting a page.
    children = list(AutomationRun.objects.filter(root_run=root).select_related('parent_action_run').order_by('created_at', 'id')[:201])
    runs = [root] + children[:200]
    run_map = {run.pk: run for run in runs}
    parent_map = {run.pk: run.parent_action_run.automation_run_id if run.parent_action_run_id else None for run in runs}

    def depth(run_id):
        seen = set()
        while run_id != root.pk and run_id in parent_map and run_id not in seen:
            seen.add(run_id)
            run_id = parent_map[run_id]
        return min(len(seen), 8)

    events = []

    def event(at, title, run, *, label='', status='', message='', evidence=None, until=None):
        if at is None:
            return
        events.append({'at': at, 'title': title, 'run_name': run_name(run), 'depth': depth(run.pk), 'label': label, 'status': status, 'message': message, 'evidence': evidence or [], 'until': until})

    for run in runs:
        event(run.created_at, '실행 요청', run)
        event(run.started_at, '실행 시작', run)
        event(run.finished_at, '실행 종료', run, label=LABELS.get(run.status, run.status), status=run.status, message=run.message)
        for step in (run.graph_snapshot or {}).get('history_steps', []):
            at = parse_datetime(str(step.get('evaluated_at') or ''))
            if at is None or timezone.is_naive(at):
                continue
            matched = step.get('matched')
            label = '조건 충족' if matched is True else '조건 불충족' if matched is False else '판단 기록 없음'
            event(at, f"실행 단계 {step.get('order', '')} 조건", run, label=label, evidence=step.get('evidence') or [])
    nodes = list(AutomationNodeRun.objects.filter(automation_run_id__in=run_map).order_by('created_at', 'id')[:1001])
    for node in nodes[:1000]:
        if node.node_type in {'start', 'end', 'action'}:
            continue
        run = run_map[node.automation_run_id]
        snapshot = run.graph_snapshot or {}
        config = next((n for n in snapshot.get('nodes', []) if str(n.get('key')) == str(node.node_key)), {})
        result = node.result or {}
        if node.node_type == 'condition':
            matched = result.get('matched')
            label = '조건 충족' if matched is True else '조건 불충족' if matched is False else '판단 기록 없음'
            evidence = result.get('evidence') or []
            event(node.started_at or node.created_at, config.get('title') or '조건 평가', run, label=label, status=node.status, message=node.message, evidence=evidence)
        else:
            event(node.created_at, config.get('title') or '지연 대기', run, label=LABELS.get(node.status, node.status), status=node.status, until=node.available_at)
    actions = list(ActionRun.objects.filter(root_run=root, automation_run_id__in=run_map).select_related('device').order_by('created_at', 'id')[:1001])
    for action in actions[:1000]:
        run = run_map[action.automation_run_id]
        if action.action_type == Action.Type.AUTOMATION:
            title = (action.target_automation_name or '삭제된 시퀀스') + ' 호출'
        else:
            device_name = action.device.name if action.device else (run.trigger_payload or {}).get('device_name', '삭제된 기기')
            title = device_name + ' · ' + action_label(action.function, action.device.device_type if action.device else '')
            if action.parameter:
                import json
                title += ' · ' + json.dumps(action.parameter, ensure_ascii=False)[:256]
        event(action.started_at or action.created_at, title, run, label='처리 시작' if action.started_at else LABELS.get(action.status, action.status), status='' if action.started_at else action.status, message=action.message if not action.finished_at else '', until=action.available_at if action.status == 'waiting' else None)
        if action.finished_at:
            event(action.finished_at, title + ' 처리 종료', run, label=LABELS.get(action.status, action.status), status=action.status, message=action.message)
    events.sort(key=lambda item: item['at'])
    return {'history_run': root, 'history_name': run_name(root), 'history_events': events[:1000], 'history_truncated': len(events) > 1000 or len(nodes) > 1000 or len(actions) > 1000 or len(children) > 200, 'history_duration': duration(root.started_at, root.finished_at)}

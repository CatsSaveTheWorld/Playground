"""Read-only, bounded projections of recorded executions. Never query live state."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.core.paginator import Paginator
from django.db.models import Case, CharField, DurationField, Exists, ExpressionWrapper, F, OuterRef, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .device_actions import DeviceActionRegistry
from .models import Action, ActionRun, AutomationNodeRun, AutomationRun, Device

KST = ZoneInfo('Asia/Seoul')
LABELS = {'pending': '대기', 'waiting': '지연 대기', 'running': '실행 중', 'success': '완료', 'failed': '실패', 'cancelled': '취소', 'skipped': '동작 없음', 'no_action': '동작 없음'}


class HistoryFilter(forms.Form):
    period = forms.ChoiceField(required=False, label='기간', choices=[('today', '오늘'), ('hour', '최근 1시간'), ('week', '최근 7일'), ('custom', '직접 지정'), ('all', '전체 기간')])
    start = forms.DateField(required=False, label='시작일', widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='종료일', widget=forms.DateInput(attrs={'type': 'date'}))
    status = forms.ChoiceField(required=False, label='결과', choices=[('', '전체')] + [(key, LABELS[key]) for key in ['success', 'failed', 'cancelled', 'no_action', 'running', 'pending', 'waiting']])
    include_no_action = forms.BooleanField(required=False, label='동작 없음 포함')
    q = forms.CharField(required=False, max_length=200, label='검색어', widget=forms.TextInput(attrs={'placeholder': '작업명 · 기기명 · 오류 메시지', 'type': 'search'}))
    tasks = forms.MultipleChoiceField(required=False, label='작업', widget=forms.CheckboxSelectMultiple)
    devices = forms.MultipleChoiceField(required=False, label='관련 기기', widget=forms.CheckboxSelectMultiple)
    source = forms.MultipleChoiceField(required=False, label='실행 계기', choices=AutomationRun.Source.choices, widget=forms.CheckboxSelectMultiple)
    functions = forms.MultipleChoiceField(required=False, label='실행 동작', widget=forms.CheckboxSelectMultiple)
    exclude_tasks = forms.MultipleChoiceField(required=False, label='제외할 작업', widget=forms.CheckboxSelectMultiple)
    run_id = forms.IntegerField(required=False, min_value=1, max_value=9223372036854775807, label='실행 ID')
    min_duration = forms.DecimalField(required=False, min_value=0, max_value=31536000, decimal_places=3, max_digits=11, label='최소 소요 시간(초)', widget=forms.NumberInput(attrs={'step': '0.001', 'placeholder': '예: 10'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Recorded names keep deleted/renamed tasks searchable, without using current rules.
        names = list(AutomationRun.objects.exclude(automation_name='').order_by('automation_name').values_list('automation_name', flat=True).distinct())
        task_choices = [(name, '창문 안전 정지' if name.startswith('__window_pusher_safety_stop__') else name) for name in names]
        self.fields['tasks'].choices = task_choices
        self.fields['exclude_tasks'].choices = task_choices
        self.fields['devices'].choices = [(str(pk), name) for pk, name in Device.objects.order_by('name', 'pk').values_list('pk', 'name')]
        known = {action.code: action.display_name for actions in DeviceActionRegistry._ACTIONS.values() for action in actions}
        codes = ActionRun.objects.filter(action_type=Action.Type.DEVICE).exclude(function='').order_by('function').values_list('function', flat=True).distinct()
        self.fields['functions'].choices = [(code, known.get(code, action_label(code))) for code in codes]

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
    commands = ActionRun.objects.filter(root_run_id=OuterRef('pk'), action_type=Action.Type.DEVICE).filter(Q(started_at__isnull=False) | Q(status__in=['success', 'failed']))
    return AutomationRun.objects.filter(root_run__isnull=True, parent_action_run__isnull=True).select_related('automation').annotate(
        history_at=Coalesce('started_at', 'created_at'),
        has_commands=Exists(commands),
        history_status=Case(
            When(Q(status=AutomationRun.Status.SKIPPED) | (Q(status=AutomationRun.Status.SUCCESS) & ~Exists(commands)), then=Value('no_action')),
            default='status', output_field=CharField(),
        ),
        history_kind=Case(When(trigger_payload__kind='device_control', then=Value('기기 제어')), default=Value('자동화'), output_field=CharField()),
    )


ADVANCED_FIELDS = ['source', 'functions', 'exclude_tasks', 'run_id', 'min_duration']


def history_data(request):
    data = request.GET.copy()
    if not data.get('period'):
        data['period'] = 'custom' if 'start' in data or 'end' in data else 'today'
    if data.get('status') == 'no_action':
        data['include_no_action'] = 'on'
    return data


def history_context(request):
    data = history_data(request)
    form = HistoryFilter(data)
    runs = root_runs().defer('graph_snapshot', 'trigger_payload')
    if form.is_valid():
        cleaned = form.cleaned_data
        now = timezone.localtime(timezone.now(), KST)
        period = cleaned.get('period')
        if period == 'today':
            runs = runs.filter(history_at__gte=datetime.combine(now.date(), time.min, KST), history_at__lt=datetime.combine(now.date() + timedelta(days=1), time.min, KST))
        elif period in ['hour', 'week']:
            runs = runs.filter(history_at__gte=now - timedelta(hours=1) if period == 'hour' else now - timedelta(days=7), history_at__lte=now)
        if period == 'custom' and cleaned.get('start'):
            runs = runs.filter(history_at__gte=datetime.combine(cleaned['start'], time.min, KST))
        if period == 'custom' and cleaned.get('end'):
            runs = runs.filter(history_at__lt=datetime.combine(cleaned['end'] + timedelta(days=1), time.min, KST))
        if cleaned.get('status'):
            runs = runs.filter(history_status=cleaned['status'])
        if not cleaned.get('include_no_action') and cleaned.get('status') != 'no_action':
            runs = runs.exclude(history_status='no_action')
        children = AutomationRun.objects.filter(root_run_id=OuterRef('pk'))
        actions = ActionRun.objects.filter(root_run_id=OuterRef('pk'))
        if cleaned.get('tasks'):
            names = cleaned['tasks']
            runs = runs.filter(Q(automation_name__in=names) | Exists(children.filter(automation_name__in=names)) | Exists(actions.filter(target_automation_name__in=names)))
        if cleaned.get('exclude_tasks'):
            names = cleaned['exclude_tasks']
            runs = runs.exclude(Q(automation_name__in=names) | Exists(children.filter(automation_name__in=names)) | Exists(actions.filter(target_automation_name__in=names)))
        if cleaned.get('source'):
            runs = runs.filter(source__in=cleaned['source'])
        command_matches = actions.filter(action_type=Action.Type.DEVICE)
        if cleaned.get('devices'):
            command_matches = command_matches.filter(device_id__in=cleaned['devices'])
        if cleaned.get('functions'):
            command_matches = command_matches.filter(function__in=cleaned['functions'])
        if cleaned.get('devices') or cleaned.get('functions'):
            runs = runs.filter(Exists(command_matches))
        if cleaned.get('run_id'):
            runs = runs.filter(Q(pk=cleaned['run_id']) | Exists(children.filter(pk=cleaned['run_id'])))
        if cleaned.get('min_duration') is not None:
            runs = runs.annotate(history_elapsed=ExpressionWrapper(F('finished_at') - F('started_at'), output_field=DurationField())).filter(history_elapsed__gte=timedelta(seconds=float(cleaned['min_duration'])))
        if cleaned.get('q'):
            query = cleaned['q']
            run_text = Q(automation_name__icontains=query) | Q(message__icontains=query) | Q(trigger_payload__device_name__icontains=query)
            matching_codes = {action.code for group in DeviceActionRegistry._ACTIONS.values() for action in group if query.casefold() in action.display_name.casefold()}
            action_text = Q(message__icontains=query) | Q(device__name__icontains=query) | Q(function__icontains=query) | Q(target_automation_name__icontains=query) | Q(function__in=matching_codes)
            node_matches = AutomationNodeRun.objects.filter(Q(automation_run_id=OuterRef('pk')) | Q(automation_run__root_run_id=OuterRef('pk')), message__icontains=query)
            runs = runs.filter(run_text | Exists(children.filter(run_text)) | Exists(actions.filter(action_text)) | Exists(node_matches))
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
    for name in list(params):
        values = [value for value in params.getlist(name) if value]
        if values:
            params.setlist(name, values)
        else:
            params.pop(name, None)
    chips = []
    for name in form.fields:
        values = params.getlist(name)
        if not any(values) or (name in ['start', 'end'] and data.get('period') != 'custom') or (name == 'period' and data.get('period') == 'all'):
            continue
        field = form.fields[name]
        choices = dict(getattr(field, 'choices', []))
        for value in values:
            if not value:
                continue
            removed = params.copy()
            removed.setlist(name, [v for v in values if v != value])
            if name == 'period':
                removed['period'] = 'all'
                removed.pop('start', None)
                removed.pop('end', None)
            label = choices.get(value, value)
            chips.append({'label': field.label if name == 'include_no_action' else f'{field.label}: {label}', 'query': removed.urlencode()})
    if not data.get('include_no_action') and data.get('status') != 'no_action':
        included = params.copy()
        included['include_no_action'] = 'on'
        chips.append({'label': '동작 없음 제외', 'query': included.urlencode()})
    return {'history_filter': form, 'history_page': page, 'history_query': params.urlencode(),
            'history_chips': chips, 'history_advanced': any(data.get(name) for name in ADVANCED_FIELDS),
            'history_has_match': any(data.get(name) for name in ['q', 'tasks', 'devices', 'functions'])}


def execution_detail_context(root, search=None):
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

    def event(at, title, run, *, label='', status='', message='', evidence=None, until=None, action=None):
        if at is None:
            return
        matched = False
        if search:
            query = search.get('q', '').strip().casefold()
            matched = bool(query and query in ' '.join([title, message or '', run.automation_name, action.function if action else '']).casefold())
            matched = matched or run.automation_name in search.getlist('tasks')
            if action:
                matched = matched or str(action.device_id) in search.getlist('devices') or action.function in search.getlist('functions') or action.target_automation_name in search.getlist('tasks')
        events.append({'at': at, 'title': title, 'run_name': run_name(run), 'depth': depth(run.pk), 'label': label, 'status': status, 'message': message, 'evidence': evidence or [], 'until': until, 'search_match': matched})

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
        event(action.started_at or action.created_at, title, run, label='처리 시작' if action.started_at else LABELS.get(action.status, action.status), status='' if action.started_at else action.status, message=action.message if not action.finished_at else '', until=action.available_at if action.status == 'waiting' else None, action=action)
        if action.finished_at:
            event(action.finished_at, title + ' 처리 종료', run, label=LABELS.get(action.status, action.status), status=action.status, message=action.message, action=action)
    events.sort(key=lambda item: item['at'])
    return {'history_run': root, 'history_name': run_name(root), 'history_events': events[:1000], 'history_truncated': len(events) > 1000 or len(nodes) > 1000 or len(actions) > 1000 or len(children) > 200, 'history_duration': duration(root.started_at, root.finished_at)}

from datetime import datetime
from zoneinfo import ZoneInfo

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def history_run_name(run):
    from iotcore.execution_history import run_name
    return run_name(run)


@register.filter
def history_clock(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return '기록 없음'
    if not isinstance(value, datetime):
        return '기록 없음'
    if timezone.is_naive(value):
        return '시각대 기록 없음'
    return timezone.localtime(value, ZoneInfo('Asia/Seoul')).strftime('%Y.%m.%d %H:%M:%S.%f')[:-3]


@register.filter
def history_value(value):
    if value is True:
        return '참'
    if value is False:
        return '거짓'
    if value is None:
        return '값 없음'
    return str(value)[:256]


@register.filter
def history_operator(value):
    return {'eq': '=', 'ne': '≠', 'lt': '<', 'lte': '≤', 'gt': '>', 'gte': '≥', 'changed': '변경', 'changed_to': '변경 후 값'}.get(value, value)


@register.filter
def history_key(value):
    return {'temperature': '온도', 'humidity': '습도', 'pressure': '기압', 'battery': '배터리', 'power': '전원', 'window_state': '창문 상태', 'position': '위치', 'precipitation_probability': '강수 확률'}.get(value, value)


@register.filter
def history_unit(value):
    return {'temperature': '°C', 'humidity': '%', 'pressure': ' hPa', 'precipitation_probability': '%'}.get(value, '')

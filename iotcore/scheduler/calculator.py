from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_time

from ..models import AutomationCondition, AutomationTrigger


INTERVAL_UNITS = {
    "seconds": "초",
    "minutes": "분",
    "hours": "시간",
    "days": "일",
}


def format_korean_time(value):
    """Format a time as Korean 12-hour clock text."""
    hour = value.hour
    period = "오전" if hour < 12 else "오후"
    display_hour = hour % 12 or 12
    return f"{period} {display_hour}:{value.minute:02d}"


def calculate_next_schedule(config, after=None, previous_next=None):
    """Return the next due time for a schedule-condition config."""
    after = after or timezone.now()
    local_after = timezone.localtime(after)
    config = config or {}
    schedule_type = config.get("schedule_type")

    if schedule_type == AutomationTrigger.ScheduleType.ONCE:
        run_at = parse_datetime(str(config.get("run_at", "")))
        if run_at is None:
            raise ValidationError("한 번 실행 시각이 올바르지 않습니다.")
        if timezone.is_naive(run_at):
            run_at = timezone.make_aware(run_at)
        return run_at if run_at > after else None

    if schedule_type in {
        AutomationTrigger.ScheduleType.DAILY,
        AutomationTrigger.ScheduleType.WEEKLY,
    }:
        run_time = parse_time(str(config.get("time", "")))
        if run_time is None:
            raise ValidationError("실행 시간이 올바르지 않습니다.")

        weekdays = None
        if schedule_type == AutomationTrigger.ScheduleType.WEEKLY:
            try:
                weekdays = {int(day) for day in config.get("weekdays", [])}
            except (TypeError, ValueError):
                weekdays = set()
            if not weekdays or not weekdays.issubset(set(range(7))):
                raise ValidationError("실행 요일을 하나 이상 선택하세요.")

        for days_ahead in range(8):
            candidate_date = local_after.date() + timedelta(days=days_ahead)
            if weekdays is not None and candidate_date.weekday() not in weekdays:
                continue
            candidate = timezone.make_aware(
                datetime.combine(candidate_date, run_time),
                timezone.get_current_timezone(),
            )
            if candidate > after:
                return candidate
        return None

    if schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
        try:
            every = int(config.get("every", 0))
        except (TypeError, ValueError):
            every = 0
        unit = config.get("unit")
        if every <= 0 or unit not in INTERVAL_UNITS:
            raise ValidationError("실행 간격이 올바르지 않습니다.")
        delta = timedelta(**{unit: every})
        candidate = previous_next or (after + delta)
        while candidate <= after:
            candidate += delta
        return candidate

    raise ValidationError("지원하지 않는 예약 시간 유형입니다.")


def calculate_next_run(trigger, after=None):
    """Backward-compatible trigger API.

    Legacy TIME triggers still read ``trigger.config``. Current trigger sets
    derive their schedule from the single SCHEDULE condition they own.
    """
    if trigger.trigger_type == AutomationTrigger.TriggerType.TIME:
        return calculate_next_schedule(
            trigger.config or {},
            after=after,
            previous_next=trigger.next_run_at,
        )
    if trigger.trigger_type == AutomationTrigger.TriggerType.SET and trigger.pk:
        condition = (
            trigger.conditions
            .filter(condition_type=AutomationCondition.ConditionType.SCHEDULE)
            .order_by("order", "id")
            .first()
        )
        if condition is None:
            return None
        return calculate_next_schedule(
            condition.config or {},
            after=after,
            previous_next=trigger.next_run_at,
        )
    return None


def _operator_label(operator):
    return {
        "eq": "=",
        "ne": "≠",
        "gt": ">",
        "gte": "≥",
        "lt": "<",
        "lte": "≤",
        "changed": "변경됨",
        "changed_to": "변경 후 =",
        "received": "수신",
    }.get(operator, operator or "=")


def describe_schedule(config):
    config = config or {}
    schedule_type = config.get("schedule_type")
    if schedule_type == AutomationTrigger.ScheduleType.ONCE:
        run_at = parse_datetime(str(config.get("run_at", "")))
        if run_at is None:
            return f"{config.get('run_at', '-')} 한 번"
        if timezone.is_naive(run_at):
            run_at = timezone.make_aware(run_at)
        local_run_at = timezone.localtime(run_at)
        return f"{local_run_at:%Y-%m-%d} {format_korean_time(local_run_at)} 한 번"
    if schedule_type == AutomationTrigger.ScheduleType.DAILY:
        run_time = parse_time(str(config.get("time", "")))
        return f"매일 {format_korean_time(run_time)}" if run_time else "매일 -"
    if schedule_type == AutomationTrigger.ScheduleType.WEEKLY:
        labels = ["월", "화", "수", "목", "금", "토", "일"]
        weekday_numbers = {
            int(day)
            for day in config.get("weekdays", [])
            if str(day).isdigit() and 0 <= int(day) <= 6
        }
        days = "매일" if weekday_numbers == set(range(7)) else (
            "매주 " + ", ".join(labels[day] for day in sorted(weekday_numbers))
        )
        run_time = parse_time(str(config.get("time", "")))
        return f"{days} {format_korean_time(run_time) if run_time else '-'}"
    if schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
        unit = INTERVAL_UNITS.get(config.get("unit"), config.get("unit", ""))
        return f"{config.get('every', '-')} {unit}마다"
    return "예약 시간 미설정"


def describe_trigger(trigger):
    if trigger.trigger_type == AutomationTrigger.TriggerType.SET:
        conditions = list(trigger.conditions.order_by("order", "id"))
        if not conditions:
            return "조건 없음"
        joiner = " AND " if trigger.condition_operator == AutomationTrigger.ConditionOperator.AND else " OR "
        return joiner.join(describe_condition(condition) for condition in conditions)

    # Legacy summaries.
    config = trigger.config or {}
    if trigger.trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
        topic = config.get("topic", "-")
        if any(key in config for key in ("field", "operator", "value")):
            field = config.get("field") or "value"
            operator = _operator_label(config.get("operator"))
            value = config.get("value", "")
            return f"{topic} · {field} {operator} {value}"
        return f"{topic} 메시지 수신"
    if trigger.trigger_type == AutomationTrigger.TriggerType.DEVICE_STATE:
        label = config.get("device_name") or config.get("device_uid") or "기기"
        return f"{label} 상태 변경"
    return describe_schedule(config)


def describe_condition(condition):
    config = condition.config or {}
    if condition.condition_type == AutomationCondition.ConditionType.SCHEDULE:
        return describe_schedule(config)
    if condition.condition_type == AutomationCondition.ConditionType.TIME_WINDOW:
        return f"{config.get('start', '-')} ~ {config.get('end', '-')}"

    operator = _operator_label(config.get("operator"))
    if condition.condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
        device = (
            config.get("device_name")
            or config.get("device_uid")
            or config.get("topic")
            or "기기"
        )
        key = config.get("key") or "value"
        if config.get("operator") == "changed":
            return f"{device} · {key} {operator}"
        return f"{device} · {key} {operator} {config.get('value', '')}"
    if condition.condition_type == AutomationCondition.ConditionType.MQTT_EVENT:
        topic = config.get("topic") or "MQTT"
        if config.get("operator") == "received":
            return f"{topic} · 메시지 수신"
        field = config.get("field") or "value"
        if config.get("operator") == "changed":
            return f"{topic} · {field} {operator}"
        return f"{topic} · {field} {operator} {config.get('value', '')}"
    if condition.condition_type == AutomationCondition.ConditionType.EVENT_VALUE:
        field = config.get("field") or "value"
        if config.get("operator") == "changed":
            return f"트리거 · {field} {operator}"
        return f"트리거 · {field} {operator} {config.get('value', '')}"
    return "설정되지 않음"

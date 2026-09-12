from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_time

from ..models import Step, Trigger


INTERVAL_UNITS = {
    "seconds": "초",
    "minutes": "분",
    "hours": "시간",
    "days": "일",
}


def _schedule_weekdays(config):
    try:
        weekdays = {int(day) for day in (config or {}).get("weekdays", [])}
    except (TypeError, ValueError):
        return set()
    return weekdays if weekdays.issubset(set(range(7))) else set()


def is_schedule_window(config):
    config = config or {}
    return (
        config.get("schedule_type") == Trigger.ScheduleType.WEEKLY
        and config.get("time_mode") == Trigger.ScheduleTimeMode.WINDOW
    )


def schedule_window_matches(config, now=None):
    config = config or {}
    if not is_schedule_window(config):
        return False
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    weekdays = _schedule_weekdays(config)
    if not weekdays:
        return False
    start = parse_time(str(config.get("start", "")))
    if start is None:
        return False
    raw_end = config.get("end")
    end = parse_time(str(raw_end)) if raw_end not in (None, "") else None
    current = local_now.time().replace(tzinfo=None)
    today = local_now.weekday()
    if end is None:
        return today in weekdays and current >= start
    if start <= end:
        return today in weekdays and start <= current <= end
    if current >= start:
        return today in weekdays
    if current <= end:
        return ((today - 1) % 7) in weekdays
    return False


def calculate_next_schedule(config, after=None, previous_next=None):
    after = after or timezone.now()
    local_after = timezone.localtime(after)
    config = config or {}
    schedule_type = config.get("schedule_type")

    if schedule_type == Trigger.ScheduleType.ONCE:
        run_at = parse_datetime(str(config.get("run_at", "")))
        if run_at is None:
            raise ValidationError("한 번 실행 시각이 올바르지 않습니다.")
        if timezone.is_naive(run_at):
            run_at = timezone.make_aware(run_at, timezone.get_current_timezone())
        return run_at if run_at > after else None

    if schedule_type == Trigger.ScheduleType.WEEKLY:
        if is_schedule_window(config):
            return None
        run_time = parse_time(str(config.get("time", "")))
        if run_time is None:
            raise ValidationError("실행 시간이 올바르지 않습니다.")
        weekdays = _schedule_weekdays(config)
        if not weekdays:
            raise ValidationError("실행 요일을 하나 이상 선택하세요.")
        for days_ahead in range(8):
            candidate_date = local_after.date() + timedelta(days=days_ahead)
            if candidate_date.weekday() not in weekdays:
                continue
            candidate = timezone.make_aware(
                datetime.combine(candidate_date, run_time),
                timezone.get_current_timezone(),
            )
            if candidate > after:
                return candidate
        return None

    if schedule_type == Trigger.ScheduleType.INTERVAL:
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


def calculate_next_run(step, after=None):
    trigger = (
        step.triggers
        .filter(trigger_type=Trigger.Type.SCHEDULE)
        .order_by("order", "id")
        .first()
    )
    if trigger is None:
        return None
    return calculate_next_schedule(
        trigger.config or {},
        after=after,
        previous_next=step.next_run_at,
    )


def format_korean_time(value):
    hour = value.hour
    period = "오전" if hour < 12 else "오후"
    return f"{period} {hour % 12 or 12}:{value.minute:02d}"


def _operator_label(operator):
    return {
        "eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤",
        "changed": "변경됨", "changed_to": "변경 후 =", "received": "수신",
    }.get(operator, operator or "=")


def describe_schedule(config):
    config = config or {}
    schedule_type = config.get("schedule_type")
    if schedule_type == Trigger.ScheduleType.ONCE:
        run_at = parse_datetime(str(config.get("run_at", "")))
        if run_at is None:
            return f"{config.get('run_at', '-')} 한 번"
        if timezone.is_naive(run_at):
            run_at = timezone.make_aware(run_at, timezone.get_current_timezone())
        run_at = timezone.localtime(run_at)
        return f"{run_at:%Y-%m-%d} {format_korean_time(run_at)} 한 번"
    if schedule_type == Trigger.ScheduleType.WEEKLY:
        labels = ["월", "화", "수", "목", "금", "토", "일"]
        weekday_numbers = _schedule_weekdays(config)
        days = "매일" if weekday_numbers == set(range(7)) else "매주 " + ", ".join(labels[d] for d in sorted(weekday_numbers))
        if is_schedule_window(config):
            start = parse_time(str(config.get("start", "")))
            raw_end = config.get("end")
            end = parse_time(str(raw_end)) if raw_end not in (None, "") else None
            if start is None:
                return f"{days} 시간대 미설정"
            return f"{days} {format_korean_time(start)} 이후" if end is None else f"{days} {format_korean_time(start)} ~ {format_korean_time(end)}"
        run_time = parse_time(str(config.get("time", "")))
        return f"{days} {format_korean_time(run_time) if run_time else '-'}"
    if schedule_type == Trigger.ScheduleType.INTERVAL:
        return f"{config.get('every', '-')} {INTERVAL_UNITS.get(config.get('unit'), config.get('unit', ''))}마다"
    return "예약 시간 미설정"


def describe_trigger(trigger):
    config = trigger.config or {}
    op = _operator_label(config.get("operator"))
    if trigger.trigger_type == Trigger.Type.SCHEDULE:
        return describe_schedule(config)
    if trigger.trigger_type == Trigger.Type.DEVICE_STATE:
        device = config.get("device_name") or config.get("device_uid") or config.get("topic") or "기기"
        key = config.get("key") or "value"
        return f"{device} · {key} {op}" if config.get("operator") == "changed" else f"{device} · {key} {op} {config.get('value', '')}"
    if trigger.trigger_type == Trigger.Type.MQTT_EVENT:
        topic = config.get("topic") or "MQTT"
        if config.get("operator") == "received":
            return f"{topic} · 메시지 수신"
        field = config.get("field") or "value"
        return f"{topic} · {field} {op}" if config.get("operator") == "changed" else f"{topic} · {field} {op} {config.get('value', '')}"
    if trigger.trigger_type == Trigger.Type.WEATHER:
        metric = config.get("metric") or "temperature"
        label, unit = {"temperature": ("현재 기온", "°C"), "humidity": ("현재 습도", "%"), "precipitation_probability": ("강수 확률", "%")}.get(metric, (metric, ""))
        return f"현재 날씨 · {label} {op} {config.get('value', '')}{unit}"
    return "설정되지 않음"


def describe_step(step):
    triggers = list(step.triggers.order_by("order", "id"))
    if not triggers:
        return "조건 없음"
    joiner = " AND " if step.trigger_operator == Step.TriggerOperator.AND else " OR "
    return joiner.join(describe_trigger(trigger) for trigger in triggers)

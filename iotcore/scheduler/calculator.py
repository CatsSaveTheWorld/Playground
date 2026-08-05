from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_time

from ..models import AutomationTrigger


INTERVAL_UNITS = {
    "seconds": "초",
    "minutes": "분",
    "hours": "시간",
    "days": "일",
}


def calculate_next_run(trigger, after=None):
    if trigger.trigger_type != AutomationTrigger.TriggerType.TIME:
        return None

    after = after or timezone.now()
    local_after = timezone.localtime(after)
    config = trigger.config or {}
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
        candidate = trigger.next_run_at or (after + delta)
        while candidate <= after:
            candidate += delta
        return candidate

    raise ValidationError("지원하지 않는 예약 시간 유형입니다.")


def describe_trigger(trigger):
    config = trigger.config or {}
    if trigger.trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
        field = config.get("field") or "value"
        operator = {
            "eq": "=",
            "ne": "≠",
            "changed": "변경됨",
            "changed_to": "변경 후 =",
        }.get(config.get("operator"), config.get("operator", "="))
        value = config.get("value", "")
        return f"{config.get('topic', '-')} · {field} {operator} {value}"

    schedule_type = config.get("schedule_type")
    if schedule_type == AutomationTrigger.ScheduleType.ONCE:
        return f"{config.get('run_at', '-')} 한 번"
    if schedule_type == AutomationTrigger.ScheduleType.DAILY:
        return f"매일 {config.get('time', '-')}"
    if schedule_type == AutomationTrigger.ScheduleType.WEEKLY:
        labels = ["월", "화", "수", "목", "금", "토", "일"]
        days = ", ".join(
            labels[int(day)]
            for day in config.get("weekdays", [])
            if str(day).isdigit() and 0 <= int(day) <= 6
        )
        return f"매주 {days} {config.get('time', '-')}"
    if schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
        unit = INTERVAL_UNITS.get(config.get("unit"), config.get("unit", ""))
        return f"{config.get('every', '-')} {unit}마다"
    return "설정되지 않음"

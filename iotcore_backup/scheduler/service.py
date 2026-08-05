from datetime import datetime
import uuid

import paho.mqtt.client as mqtt
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_time

from ..models import (
    Automation,
    AutomationCondition,
    AutomationTrigger,
    DeviceState,
    SequenceRun,
)
from .calculator import calculate_next_run


_MISSING = object()


def get_nested_value(payload, path, default=_MISSING):
    value = payload
    for key in str(path or "value").split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def compare_value(operator, current, expected=None, previous=_MISSING):
    if current is _MISSING:
        return False
    if operator == "ne":
        return current != expected
    if operator == "changed":
        return previous is not _MISSING and previous != current
    if operator == "changed_to":
        return previous is not _MISSING and previous != current and current == expected
    return current == expected


class AutomationService:
    @classmethod
    def recalculate_trigger(cls, trigger, after=None):
        trigger.next_run_at = None
        if (
            trigger.enabled
            and trigger.automation.enabled
            and trigger.trigger_type == AutomationTrigger.TriggerType.TIME
        ):
            trigger.next_run_at = calculate_next_run(trigger, after=after)
            if trigger.next_run_at is None:
                trigger.enabled = False
        trigger.save(update_fields=["enabled", "next_run_at", "updated_at"])
        return trigger.next_run_at

    @classmethod
    def recalculate_automation(cls, automation, after=None):
        for trigger in automation.triggers.select_related("automation"):
            cls.recalculate_trigger(trigger, after=after)

    @classmethod
    def enqueue_due(cls, now=None):
        now = now or timezone.now()
        enqueued = []
        due_ids = list(
            AutomationTrigger.objects.filter(
                trigger_type=AutomationTrigger.TriggerType.TIME,
                enabled=True,
                automation__enabled=True,
                next_run_at__isnull=False,
                next_run_at__lte=now,
            ).values_list("id", flat=True)
        )

        for trigger_id in due_ids:
            with transaction.atomic():
                trigger = (
                    AutomationTrigger.objects
                    .select_for_update()
                    .select_related("automation__sequence")
                    .get(pk=trigger_id)
                )
                if (
                    not trigger.enabled
                    or not trigger.automation.enabled
                    or trigger.next_run_at is None
                    or trigger.next_run_at > now
                ):
                    continue

                scheduled_for = trigger.next_run_at
                sequence_run = cls._enqueue_locked(
                    trigger,
                    now=now,
                    scheduled_for=scheduled_for,
                    trigger_payload={"trigger_id": trigger.id, "type": "time"},
                )
                schedule_type = (trigger.config or {}).get("schedule_type")
                trigger.last_triggered_at = now
                trigger.next_run_at = calculate_next_run(trigger, after=now)
                if schedule_type == AutomationTrigger.ScheduleType.ONCE:
                    trigger.enabled = False
                trigger.save(
                    update_fields=[
                        "last_triggered_at",
                        "next_run_at",
                        "enabled",
                        "updated_at",
                    ]
                )
                if sequence_run is not None:
                    enqueued.append(sequence_run)

        return enqueued

    @classmethod
    def process_event(cls, topic, payload, now=None):
        now = now or timezone.now()
        previous = cls._update_device_state(topic, payload)
        event_id = str(
            payload.get("event_id")
            if isinstance(payload, dict) and payload.get("event_id")
            else uuid.uuid4().hex
        )
        enqueued = []
        trigger_ids = list(
            AutomationTrigger.objects.filter(
                trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
                enabled=True,
                automation__enabled=True,
            ).values_list("id", flat=True)
        )

        for trigger_id in trigger_ids:
            with transaction.atomic():
                trigger = (
                    AutomationTrigger.objects
                    .select_for_update()
                    .select_related("automation__sequence")
                    .get(pk=trigger_id)
                )
                config = trigger.config or {}
                if not mqtt.topic_matches_sub(str(config.get("topic", "")), topic):
                    continue
                field = config.get("field") or "value"
                current = get_nested_value(payload, field)
                if not compare_value(
                    config.get("operator") or "eq",
                    current,
                    config.get("value"),
                    previous.get(field, _MISSING),
                ):
                    continue

                sequence_run = cls._enqueue_locked(
                    trigger,
                    now=now,
                    source_event_id=event_id,
                    trigger_payload={"topic": topic, "payload": payload},
                )
                if sequence_run is not None:
                    trigger.last_triggered_at = now
                    trigger.save(update_fields=["last_triggered_at", "updated_at"])
                    enqueued.append(sequence_run)

        return enqueued

    @classmethod
    def _enqueue_locked(
        cls,
        trigger,
        now,
        scheduled_for=None,
        source_event_id=None,
        trigger_payload=None,
    ):
        automation = Automation.objects.select_for_update().get(
            pk=trigger.automation_id
        )
        if not automation.enabled:
            return None
        if (
            automation.cooldown_seconds
            and automation.last_triggered_at
            and (now - automation.last_triggered_at).total_seconds()
            < automation.cooldown_seconds
        ):
            return None
        if not cls._conditions_match(automation, now):
            return None

        if scheduled_for is not None:
            sequence_run, created = SequenceRun.objects.get_or_create(
                automation=automation,
                scheduled_for=scheduled_for,
                defaults={
                    "sequence": automation.sequence,
                    "trigger": SequenceRun.Trigger.AUTOMATION,
                    "trigger_payload": trigger_payload or {},
                },
            )
            if not created:
                return None
        else:
            sequence_run = SequenceRun.objects.create(
                automation=automation,
                sequence=automation.sequence,
                trigger=SequenceRun.Trigger.AUTOMATION,
                source_event_id=source_event_id,
                trigger_payload=trigger_payload or {},
            )

        automation.last_triggered_at = now
        automation.save(update_fields=["last_triggered_at", "updated_at"])
        return sequence_run

    @classmethod
    def _conditions_match(cls, automation, now):
        local_now = timezone.localtime(now)
        for condition in automation.conditions.all():
            config = condition.config or {}
            if condition.condition_type == AutomationCondition.ConditionType.TIME_WINDOW:
                start = parse_time(str(config.get("start", "")))
                end = parse_time(str(config.get("end", "")))
                if start is None or end is None:
                    return False
                weekdays = config.get("weekdays") or list(range(7))
                if local_now.weekday() not in {int(day) for day in weekdays}:
                    return False
                current = local_now.time().replace(tzinfo=None)
                inside = start <= current <= end if start <= end else (
                    current >= start or current <= end
                )
                if not inside:
                    return False
            elif condition.condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
                state = DeviceState.objects.filter(
                    topic=config.get("topic", ""),
                    key=config.get("key", ""),
                ).first()
                current = state.value if state is not None else _MISSING
                if not compare_value(
                    config.get("operator") or "eq",
                    current,
                    config.get("value"),
                ):
                    return False
        return True

    @staticmethod
    def _update_device_state(topic, payload):
        values = payload if isinstance(payload, dict) else {"value": payload}
        flattened = {}

        def flatten(value, prefix=""):
            if isinstance(value, dict):
                for key, child in value.items():
                    flatten(child, f"{prefix}.{key}" if prefix else str(key))
            elif prefix:
                flattened[prefix] = value

        flatten(values)
        previous = {
            state.key: state.value
            for state in DeviceState.objects.filter(
                topic=topic,
                key__in=flattened.keys(),
            )
        }
        for key, value in flattened.items():
            DeviceState.objects.update_or_create(
                topic=topic,
                key=key,
                defaults={"value": value},
            )
        return previous


# Backward-compatible import for the existing management command name.
SchedulerService = AutomationService

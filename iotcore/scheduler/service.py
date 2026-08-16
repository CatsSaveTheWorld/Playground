import uuid

import paho.mqtt.client as mqtt
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_time

from ..models import (
    Automation,
    AutomationCondition,
    AutomationRun,
    AutomationTrigger,
    Device,
    DeviceState,
)
from ..room_entry.service import RoomEntryService
from .calculator import calculate_next_run


_MISSING = object()
CANONICAL_STATE_PREFIX = "iotcore/devices"


def get_nested_value(payload, path, default=_MISSING):
    value = payload
    for key in str(path or "value").split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def flatten_payload(payload):
    values = payload if isinstance(payload, dict) else {"value": payload}
    flattened = {}

    def flatten(value, prefix=""):
        if isinstance(value, dict):
            for key, child in value.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                flatten(child, child_prefix)
        elif prefix:
            flattened[prefix] = value

    flatten(values)
    return flattened


def _coerce_ordered_pair(current, expected):
    if isinstance(current, bool) or isinstance(expected, bool):
        return current, expected
    try:
        return float(current), float(expected)
    except (TypeError, ValueError):
        return current, expected


def compare_value(operator, current, expected=None, previous=_MISSING):
    if current is _MISSING:
        return False
    if operator == "ne":
        return current != expected
    if operator == "changed":
        return previous is not _MISSING and previous != current
    if operator == "changed_to":
        return (
            previous is not _MISSING
            and previous != current
            and current == expected
        )
    if operator in {"gt", "gte", "lt", "lte"}:
        left, right = _coerce_ordered_pair(current, expected)
        try:
            if operator == "gt":
                return left > right
            if operator == "gte":
                return left >= right
            if operator == "lt":
                return left < right
            return left <= right
        except TypeError:
            return False
    return current == expected


class AutomationService:
    @classmethod
    def canonical_state_topic(cls, device):
        return f"{CANONICAL_STATE_PREFIX}/{device.device_uid}/state"

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
                    .select_related("automation")
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
                automation_run = cls._enqueue_locked(
                    trigger,
                    now=now,
                    scheduled_for=scheduled_for,
                    trigger_payload={
                        "trigger_id": trigger.id,
                        "type": "time",
                    },
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
                if automation_run is not None:
                    enqueued.append(automation_run)

        return enqueued

    @classmethod
    def process_event(cls, topic, payload, now=None):
        """Process a live MQTT message as a broad event/state-change trigger."""
        now = now or timezone.now()
        previous_raw = cls._update_device_state(topic, payload)
        device = cls._resolve_device_for_topic(topic)
        previous_device = {}
        normalized_payload = payload

        if device is not None:
            normalized_payload = cls._normalize_device_payload(payload)
            canonical_topic = cls.canonical_state_topic(device)
            if canonical_topic == topic:
                previous_device = previous_raw
            else:
                previous_device = cls._update_device_state(
                    canonical_topic,
                    normalized_payload,
                )

        event_id = cls._event_id(payload)
        raw_changed_keys = cls._changed_keys(
            payload,
            previous_raw,
            require_previous=True,
        )
        device_changed_keys = cls._changed_keys(
            normalized_payload,
            previous_device,
            require_previous=True,
        ) if device is not None else set()

        if device is not None:
            RoomEntryService.record_contact_change(
                device=device,
                payload=normalized_payload,
                previous=previous_device,
                changed_keys=device_changed_keys,
                now=now,
            )

        context = {
            "type": "mqtt_event",
            "topic": topic,
            "payload": payload,
            "previous": previous_raw,
            "changed_keys": sorted(raw_changed_keys),
            "device_id": device.pk if device is not None else None,
            "device_uid": device.device_uid if device is not None else None,
            "device_previous": previous_device,
            "device_changed_keys": sorted(device_changed_keys),
        }
        return cls._process_event_triggers(
            topic=topic,
            payload=payload,
            previous=previous_raw,
            device=device,
            device_changed_keys=device_changed_keys,
            now=now,
            source_event_id=event_id,
            trigger_payload=context,
        )

    @classmethod
    def record_device_state(
        cls,
        device,
        state_patch,
        *,
        now=None,
        source="control",
        source_event_id=None,
    ):
        """
        Update IoTCore's canonical last-known state after a successful local
        control or another non-MQTT observation, then evaluate state triggers.
        """
        if device is None or not state_patch:
            return []

        now = now or timezone.now()
        payload = cls._normalize_device_payload(state_patch)
        topic = cls.canonical_state_topic(device)
        previous = cls._update_device_state(topic, payload)
        changed_keys = cls._changed_keys(
            payload,
            previous,
            require_previous=False,
        )
        if not changed_keys:
            return []

        event_id = source_event_id or uuid.uuid4().hex
        context = {
            "type": "device_state",
            "source": source,
            "topic": topic,
            "payload": payload,
            "previous": previous,
            "changed_keys": sorted(changed_keys),
            "device_id": device.pk,
            "device_uid": device.device_uid,
            "device_previous": previous,
            "device_changed_keys": sorted(changed_keys),
        }
        return cls._process_event_triggers(
            topic=topic,
            payload=payload,
            previous=previous,
            device=device,
            device_changed_keys=changed_keys,
            now=now,
            source_event_id=event_id,
            trigger_payload=context,
        )

    @classmethod
    def _process_event_triggers(
        cls,
        *,
        topic,
        payload,
        previous,
        device,
        device_changed_keys,
        now,
        source_event_id,
        trigger_payload,
    ):
        enqueued = []
        trigger_ids = list(
            AutomationTrigger.objects.filter(
                trigger_type__in=[
                    AutomationTrigger.TriggerType.MQTT_EVENT,
                    AutomationTrigger.TriggerType.DEVICE_STATE,
                ],
                enabled=True,
                automation__enabled=True,
            ).values_list("id", flat=True)
        )

        for trigger_id in trigger_ids:
            with transaction.atomic():
                trigger = (
                    AutomationTrigger.objects
                    .select_for_update()
                    .select_related("automation")
                    .get(pk=trigger_id)
                )
                config = trigger.config or {}

                if trigger.trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
                    if not mqtt.topic_matches_sub(
                        str(config.get("topic", "")),
                        topic,
                    ):
                        continue
                    # Compatibility for pre-refactor rows until migration runs.
                    if any(key in config for key in ("field", "operator", "value")):
                        field = config.get("field") or "value"
                        current = get_nested_value(payload, field)
                        if not compare_value(
                            config.get("operator") or "eq",
                            current,
                            config.get("value"),
                            previous.get(field, _MISSING),
                        ):
                            continue
                elif trigger.trigger_type == AutomationTrigger.TriggerType.DEVICE_STATE:
                    if device is None or not device_changed_keys:
                        continue
                    if not cls._config_targets_device(config, device):
                        continue
                else:
                    continue

                automation_run = cls._enqueue_locked(
                    trigger,
                    now=now,
                    source_event_id=source_event_id,
                    trigger_payload=trigger_payload,
                )
                if automation_run is not None:
                    trigger.last_triggered_at = now
                    trigger.save(
                        update_fields=["last_triggered_at", "updated_at"]
                    )
                    enqueued.append(automation_run)

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
        if not cls._conditions_match(
            automation,
            now,
            trigger_payload=trigger_payload,
        ):
            return None

        if scheduled_for is not None:
            automation_run, created = AutomationRun.objects.get_or_create(
                automation=automation,
                scheduled_for=scheduled_for,
                defaults={"trigger_payload": trigger_payload or {}},
            )
        else:
            automation_run, created = AutomationRun.objects.get_or_create(
                automation=automation,
                source_event_id=source_event_id,
                defaults={"trigger_payload": trigger_payload or {}},
            )
        if not created:
            return None

        automation.last_triggered_at = now
        automation.save(update_fields=["last_triggered_at", "updated_at"])
        return automation_run

    @classmethod
    def _conditions_match(cls, automation, now, trigger_payload=None):
        local_now = timezone.localtime(now)
        trigger_payload = trigger_payload or {}

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

            elif condition.condition_type == AutomationCondition.ConditionType.EVENT_VALUE:
                field = config.get("field") or "value"
                current = get_nested_value(
                    trigger_payload.get("payload", {}),
                    field,
                )
                previous = (trigger_payload.get("previous") or {}).get(
                    field,
                    _MISSING,
                )
                if not compare_value(
                    config.get("operator") or "eq",
                    current,
                    config.get("value"),
                    previous,
                ):
                    return False

            elif condition.condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
                device = cls._device_from_config(config)
                state_topic = config.get("topic", "")
                if device is not None:
                    state_topic = cls.canonical_state_topic(device)
                if not state_topic:
                    return False

                key = config.get("key", "")
                state = DeviceState.objects.filter(
                    topic=state_topic,
                    key=key,
                ).first()
                current = state.value if state is not None else _MISSING
                previous = cls._condition_previous_value(
                    config=config,
                    device=device,
                    state_topic=state_topic,
                    key=key,
                    trigger_payload=trigger_payload,
                )
                if not compare_value(
                    config.get("operator") or "eq",
                    current,
                    config.get("value"),
                    previous,
                ):
                    return False

        return True

    @classmethod
    def update_device_state(cls, topic, payload):
        """Synchronize retained/current MQTT state without firing automations."""
        previous = cls._update_device_state(topic, payload)
        device = cls._resolve_device_for_topic(topic)
        if device is not None:
            canonical_topic = cls.canonical_state_topic(device)
            if canonical_topic != topic:
                cls._update_device_state(
                    canonical_topic,
                    cls._normalize_device_payload(payload),
                )
        return previous

    @staticmethod
    def _update_device_state(topic, payload):
        flattened = flatten_payload(payload)
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

    @staticmethod
    def _changed_keys(payload, previous, *, require_previous):
        changed = set()
        for key, current in flatten_payload(payload).items():
            if key not in previous:
                if not require_previous:
                    changed.add(key)
                continue
            if previous[key] != current:
                changed.add(key)
        return changed

    @staticmethod
    def _event_id(payload):
        if isinstance(payload, dict) and payload.get("event_id"):
            return str(payload["event_id"])
        return uuid.uuid4().hex

    @classmethod
    def _resolve_device_for_topic(cls, topic):
        topic = str(topic or "")
        canonical_prefix = f"{CANONICAL_STATE_PREFIX}/"
        if topic.startswith(canonical_prefix) and topic.endswith("/state"):
            device_uid = topic[len(canonical_prefix):-len("/state")]
            if device_uid and "/" not in device_uid:
                return Device.objects.filter(device_uid=device_uid).first()

        zigbee_prefix = "zigbee2mqtt/"
        if topic.startswith(zigbee_prefix):
            device_uid = topic[len(zigbee_prefix):]
            if device_uid and "/" not in device_uid:
                return Device.objects.filter(device_uid=device_uid).first()
        return None

    @staticmethod
    def _normalize_device_payload(payload):
        if not isinstance(payload, dict):
            return {"value": payload}
        normalized = dict(payload)
        state = normalized.get("state")
        if "power" not in normalized and isinstance(state, str):
            upper_state = state.upper()
            if upper_state == "ON":
                normalized["power"] = True
            elif upper_state == "OFF":
                normalized["power"] = False
        return normalized

    @classmethod
    def _device_from_config(cls, config):
        device_id = config.get("device_id")
        if device_id:
            device = Device.objects.filter(pk=device_id).first()
            if device is not None:
                return device
        device_uid = config.get("device_uid")
        if device_uid:
            return Device.objects.filter(device_uid=device_uid).first()
        return None

    @classmethod
    def _config_targets_device(cls, config, device):
        configured = cls._device_from_config(config)
        if configured is not None:
            return configured.pk == device.pk
        device_uid = config.get("device_uid")
        if device_uid:
            return str(device_uid) == str(device.device_uid)
        return False

    @classmethod
    def _condition_previous_value(
        cls,
        *,
        config,
        device,
        state_topic,
        key,
        trigger_payload,
    ):
        if device is not None:
            if (
                trigger_payload.get("device_id") == device.pk
                or trigger_payload.get("device_uid") == device.device_uid
            ):
                return (trigger_payload.get("device_previous") or {}).get(
                    key,
                    _MISSING,
                )
        if trigger_payload.get("topic") == state_topic:
            return (trigger_payload.get("previous") or {}).get(
                key,
                _MISSING,
            )
        return _MISSING


# Backward-compatible import for the existing management command name.
SchedulerService = AutomationService

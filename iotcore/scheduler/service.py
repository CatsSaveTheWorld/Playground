import math
import uuid

import paho.mqtt.client as mqtt
from django.db import transaction
from django.utils import timezone

from ..device.services.window_pusher_service import WindowPusherService
from ..models import Automation, AutomationRun, Device, DeviceState, Step, Trigger
from ..room_entry.service import RoomEntryService
from ..weather.service import KmaWeatherService
from .calculator import calculate_next_schedule, is_schedule_window, schedule_window_matches


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
        return previous is not _MISSING and previous != current and current == expected
    if operator in {"gt", "gte", "lt", "lte"}:
        left, right = _coerce_ordered_pair(current, expected)
        try:
            return {
                "gt": left > right,
                "gte": left >= right,
                "lt": left < right,
                "lte": left <= right,
            }[operator]
        except TypeError:
            return False
    return current == expected


class AutomationService:
    """Single trigger/scheduling service for both Automation types.

    Only Automations with recurring monitoring enabled (stored as
    ``automation_type=scheduled``) are woken automatically. One-shot Automations
    may still contain Triggers; Trigger presence alone never enables recurring
    monitoring, and those Triggers are evaluated on explicit execution.
    """

    _last_weather_evaluation_token = None

    @classmethod
    def canonical_state_topic(cls, device):
        return f"{CANONICAL_STATE_PREFIX}/{device.device_uid}/state"

    @classmethod
    def _schedule_trigger(cls, step):
        return (
            step.triggers
            .filter(trigger_type=Trigger.Type.SCHEDULE)
            .order_by("order", "id")
            .first()
        )

    @classmethod
    def recalculate_step(cls, step, after=None):
        previous_next = step.next_run_at
        step.next_run_at = None
        if (
            step.enabled
            and step.automation.enabled
            and step.automation.automation_type == Automation.Type.SCHEDULED
        ):
            trigger = cls._schedule_trigger(step)
            if trigger is not None:
                step.next_run_at = calculate_next_schedule(
                    trigger.config or {},
                    after=after,
                    previous_next=previous_next,
                )
        step.save(update_fields=["next_run_at", "updated_at"])
        return step.next_run_at

    @classmethod
    def recalculate_automation(cls, automation, after=None):
        for step in automation.steps.select_related("automation"):
            cls.recalculate_step(step, after=after)
            cls.refresh_step_result(step, now=after)

    @classmethod
    def refresh_step_result(cls, step, now=None):
        now = now or timezone.now()
        triggers = list(step.triggers.order_by("order", "id"))
        result = cls.trigger_list_matches(
            triggers,
            now,
            trigger_payload={},
            operator=step.trigger_operator,
            resting=True,
            empty_matches=False,
        )
        if result is None:
            return bool(step.last_result)
        if step.last_result != result:
            step.last_result = result
            step.save(update_fields=["last_result", "updated_at"])
        return result

    @classmethod
    def refresh_all_step_results(cls, now=None):
        now = now or timezone.now()
        for step in (
            Step.objects.filter(
                enabled=True,
                automation__enabled=True,
                automation__automation_type=Automation.Type.SCHEDULED,
            ).select_related("automation")
        ):
            cls.refresh_step_result(step, now=now)

    @classmethod
    def enqueue_due(cls, now=None):
        now = now or timezone.now()
        enqueued = []
        due_ids = list(
            Step.objects.filter(
                enabled=True,
                automation__enabled=True,
                automation__automation_type=Automation.Type.SCHEDULED,
                next_run_at__isnull=False,
                next_run_at__lte=now,
            ).values_list("id", flat=True)
        )
        for step_id in due_ids:
            with transaction.atomic():
                step = Step.objects.select_for_update().select_related("automation").get(pk=step_id)
                if not step.enabled or not step.automation.enabled or step.next_run_at is None or step.next_run_at > now:
                    continue
                scheduled_for = step.next_run_at
                trigger = cls._schedule_trigger(step)
                if trigger is None:
                    step.next_run_at = None
                    step.save(update_fields=["next_run_at", "updated_at"])
                    continue
                run = cls._enqueue_step_locked(
                    step,
                    now=now,
                    scheduled_for=scheduled_for,
                    source=AutomationRun.Source.SCHEDULER,
                    trigger_payload={
                        "type": "schedule",
                        "source_trigger_id": trigger.id,
                    },
                )
                step.next_run_at = calculate_next_schedule(
                    trigger.config or {},
                    after=now,
                    previous_next=scheduled_for,
                )
                step.save(update_fields=["next_run_at", "updated_at"])
                if run is not None:
                    enqueued.append(run)
        return enqueued

    @classmethod
    def process_weather_conditions(cls, now=None):
        now = now or timezone.now()
        step_ids = list(
            Step.objects.filter(
                enabled=True,
                automation__enabled=True,
                automation__automation_type=Automation.Type.SCHEDULED,
                triggers__trigger_type=Trigger.Type.WEATHER,
            ).distinct().values_list("id", flat=True)
        )
        if not step_ids:
            return []
        try:
            weather = KmaWeatherService.snapshot()
        except Exception:
            return []
        if not isinstance(weather, dict) or weather.get("stale"):
            return []
        token = cls._weather_snapshot_token(weather)
        if not token or token == cls._last_weather_evaluation_token:
            return []
        payload = cls._weather_snapshot_payload(weather)
        enqueued = []
        for step_id in step_ids:
            with transaction.atomic():
                step = Step.objects.select_for_update().select_related("automation").get(pk=step_id)
                run = cls._enqueue_step_locked(
                    step,
                    now=now,
                    source=AutomationRun.Source.WEATHER,
                    source_event_id=f"weather:{token}"[:100],
                    trigger_payload={"type": "weather", "weather_token": token, "weather": payload},
                )
                if run is not None:
                    enqueued.append(run)
        cls._last_weather_evaluation_token = token
        return enqueued

    @staticmethod
    def _weather_snapshot_token(weather):
        value = weather.get("fetched_at") or weather.get("updated_at")
        if value is None:
            return ""
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @staticmethod
    def _weather_snapshot_payload(weather):
        payload = {key: weather.get(key) for key in (
            "location", "temperature", "humidity", "precipitation_probability",
            "condition", "high", "low", "stale", "source",
        )}
        for key in ("updated_at", "fetched_at"):
            value = weather.get(key)
            payload[key] = value.isoformat() if hasattr(value, "isoformat") else value
        return payload

    @classmethod
    def process_event(cls, topic, payload, now=None):
        """Handle one live MQTT event, update state, then wake affected Steps."""
        now = now or timezone.now()
        previous_raw = cls._update_device_state(topic, payload)
        device = cls._resolve_device_for_topic(topic)
        previous_device = {}
        normalized_payload = payload
        if device is not None:
            normalized_payload = cls._normalize_device_payload(payload, device=device, topic=topic)
            canonical_topic = cls.canonical_state_topic(device)
            previous_device = previous_raw if canonical_topic == topic else cls._update_device_state(canonical_topic, normalized_payload)

        raw_changed_keys = cls._changed_keys(payload, previous_raw, require_previous=True)
        device_changed_keys = cls._changed_keys(normalized_payload, previous_device, require_previous=True) if device else set()
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
            "device_id": device.pk if device else None,
            "device_uid": device.device_uid if device else None,
            "device_previous": previous_device,
            "device_changed_keys": sorted(device_changed_keys),
        }
        return cls._process_event_steps(
            topic=topic,
            device=device,
            device_changed_keys=device_changed_keys,
            now=now,
            source=AutomationRun.Source.MQTT,
            source_event_id=cls._event_id(payload),
            trigger_payload=context,
        )

    @classmethod
    def record_device_state(cls, device, state_patch, *, now=None, source="control", source_event_id=None):
        if device is None or not state_patch:
            return []
        now = now or timezone.now()
        payload = cls._normalize_device_payload(
            state_patch,
            device=device,
            topic=cls.canonical_state_topic(device),
        )
        topic = cls.canonical_state_topic(device)
        previous = cls._update_device_state(topic, payload)
        changed_keys = cls._changed_keys(payload, previous, require_previous=False)
        if not changed_keys:
            return []
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
        return cls._process_event_steps(
            topic=topic,
            device=device,
            device_changed_keys=changed_keys,
            now=now,
            source=AutomationRun.Source.DEVICE,
            source_event_id=source_event_id or uuid.uuid4().hex,
            trigger_payload=context,
        )

    @classmethod
    def _process_event_steps(cls, *, topic, device, device_changed_keys, now, source, source_event_id, trigger_payload):
        enqueued = []
        step_ids = list(
            Step.objects.filter(
                enabled=True,
                automation__enabled=True,
                automation__automation_type=Automation.Type.SCHEDULED,
                triggers__trigger_type__in=[Trigger.Type.MQTT_EVENT, Trigger.Type.DEVICE_STATE],
            ).distinct().values_list("id", flat=True)
        )
        for step_id in step_ids:
            with transaction.atomic():
                step = Step.objects.select_for_update().select_related("automation").get(pk=step_id)
                if not cls._step_has_event_source(
                    step,
                    topic=topic,
                    device=device,
                    device_changed_keys=device_changed_keys,
                    trigger_payload=trigger_payload,
                ):
                    continue
                run = cls._enqueue_step_locked(
                    step,
                    now=now,
                    source=source,
                    source_event_id=source_event_id,
                    trigger_payload=trigger_payload,
                )
                if run is not None:
                    enqueued.append(run)
        return enqueued

    @classmethod
    def _step_has_event_source(cls, step, *, topic, device, device_changed_keys, trigger_payload):
        for trigger in step.triggers.order_by("order", "id"):
            config = trigger.config or {}
            if trigger.trigger_type == Trigger.Type.MQTT_EVENT:
                pattern = str(config.get("topic") or "")
                if pattern and mqtt.topic_matches_sub(pattern, topic):
                    return True
            elif trigger.trigger_type == Trigger.Type.DEVICE_STATE:
                if cls._device_trigger_was_affected(
                    config,
                    topic=topic,
                    device=device,
                    device_changed_keys=device_changed_keys,
                    trigger_payload=trigger_payload,
                ):
                    return True
        return False

    @classmethod
    def _enqueue_step_locked(cls, step, *, now, source, scheduled_for=None, source_event_id=None, trigger_payload=None):
        automation = Automation.objects.select_for_update().get(pk=step.automation_id)
        if not automation.enabled or not step.enabled or automation.automation_type != Automation.Type.SCHEDULED:
            return None
        actions = list(step.actions.order_by("order", "id"))
        triggers = list(step.triggers.order_by("order", "id"))
        if not actions or not triggers:
            return None

        payload = dict(trigger_payload or {})
        if any(t.trigger_type == Trigger.Type.WEATHER for t in triggers) and "weather" not in payload:
            try:
                weather = KmaWeatherService.snapshot()
            except Exception:
                weather = None
            payload["weather"] = cls._weather_snapshot_payload(weather) if isinstance(weather, dict) and not weather.get("stale") else {"stale": True}

        previous_result = bool(step.last_result)
        current_result = cls.trigger_list_matches(
            triggers, now, trigger_payload=payload, operator=step.trigger_operator,
            resting=False, empty_matches=False,
        )
        resting_result = cls.trigger_list_matches(
            triggers, now, trigger_payload=payload, operator=step.trigger_operator,
            resting=True, empty_matches=False,
        )
        if current_result is None or resting_result is None:
            return None

        def save_resting():
            if step.last_result != resting_result:
                step.last_result = resting_result
                step.save(update_fields=["last_result", "updated_at"])

        # Repeating state/event automation fires on a false -> true edge.
        if not current_result or previous_result:
            save_resting()
            return None
        if automation.cooldown_seconds and step.last_triggered_at and (now - step.last_triggered_at).total_seconds() < automation.cooldown_seconds:
            save_resting()
            return None

        payload["step_id"] = step.pk
        defaults = {
            "automation": automation,
            "automation_name": automation.name,
            "source": source,
            "trigger_payload": payload,
        }
        if scheduled_for is not None:
            run, created = AutomationRun.objects.get_or_create(step=step, scheduled_for=scheduled_for, defaults=defaults)
        else:
            run, created = AutomationRun.objects.get_or_create(step=step, source_event_id=source_event_id, defaults=defaults)
        if not created:
            save_resting()
            return None

        step.last_result = resting_result
        step.last_triggered_at = now
        step.save(update_fields=["last_result", "last_triggered_at", "updated_at"])
        automation.last_triggered_at = now
        automation.save(update_fields=["last_triggered_at", "updated_at"])
        return run

    @classmethod
    def trigger_list_matches(cls, triggers, now, trigger_payload=None, *, operator=Step.TriggerOperator.AND, resting=False, empty_matches=True):
        triggers = list(triggers)
        if not triggers:
            return empty_matches
        results = [cls.trigger_matches(t, now, trigger_payload=trigger_payload, resting=resting) for t in triggers]
        if operator == Step.TriggerOperator.OR:
            if any(r is True for r in results):
                return True
            return None if any(r is None for r in results) else False
        if any(r is False for r in results):
            return False
        return None if any(r is None for r in results) else True

    @classmethod
    def trigger_matches(cls, trigger, now, trigger_payload=None, *, resting=False):
        trigger_payload = trigger_payload or {}
        config = trigger.config or {}

        if trigger.trigger_type == Trigger.Type.SCHEDULE:
            if is_schedule_window(config):
                return schedule_window_matches(config, now=now)
            if resting:
                return False
            try:
                source_trigger_id = int(trigger_payload.get("source_trigger_id"))
            except (TypeError, ValueError):
                return False
            return source_trigger_id == trigger.pk

        if trigger.trigger_type == Trigger.Type.MQTT_EVENT:
            if resting:
                return False
            pattern = str(config.get("topic") or "")
            event_topic = str(trigger_payload.get("topic") or "")
            if not pattern or not event_topic or not mqtt.topic_matches_sub(pattern, event_topic):
                return False
            operator = config.get("operator") or "received"
            if operator == "received":
                return True
            field = config.get("field") or "value"
            current = get_nested_value(trigger_payload.get("payload", {}), field)
            previous = (trigger_payload.get("previous") or {}).get(field, _MISSING)
            return compare_value(operator, current, config.get("value"), previous)

        if trigger.trigger_type == Trigger.Type.DEVICE_STATE:
            operator = config.get("operator") or "eq"
            key = config.get("key", "")
            device = cls._device_from_config(config)
            state_topic = config.get("topic", "")
            if device is not None:
                state_topic = cls.canonical_state_topic(device)
            if not state_topic:
                return False
            if operator in {"changed", "changed_to"} and resting:
                return False
            if key == "*":
                if operator != "changed":
                    return False
                return cls._device_trigger_was_affected(
                    config,
                    topic=str(trigger_payload.get("topic") or ""),
                    device=cls._device_from_event_payload(trigger_payload),
                    device_changed_keys=set(trigger_payload.get("device_changed_keys") or []),
                    trigger_payload=trigger_payload,
                )
            state = DeviceState.objects.filter(topic=state_topic, key=key).first()
            # Schema-created rows use JSON null to mean "unknown".  Treat that
            # exactly like a missing observation so an unknown power state does
            # not accidentally satisfy e.g. ``power != true``.
            current = (
                state.value
                if state is not None and state.value is not None
                else _MISSING
            )
            previous = cls._trigger_previous_value(
                config=config, device=device, state_topic=state_topic, key=key,
                trigger_payload=trigger_payload,
            )
            if previous is None:
                previous = _MISSING
            return compare_value(operator, current, config.get("value"), previous)

        if trigger.trigger_type == Trigger.Type.WEATHER:
            metric = config.get("metric")
            operator = config.get("operator")
            if metric not in {"temperature", "humidity", "precipitation_probability"} or operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                return False
            weather = trigger_payload.get("weather")
            if weather is None:
                if resting:
                    return None
                try:
                    weather = KmaWeatherService.snapshot()
                except Exception:
                    return None
            if not isinstance(weather, dict) or weather.get("stale"):
                return None
            current, expected = weather.get(metric), config.get("value")
            if current is None or expected is None:
                return None
            try:
                current, expected = float(current), float(expected)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(current) or not math.isfinite(expected):
                return None
            return compare_value(operator, current, expected)

        return False

    @classmethod
    def update_device_state(cls, topic, payload):
        previous = cls._update_device_state(topic, payload)
        device = cls._resolve_device_for_topic(topic)
        if device is not None:
            canonical_topic = cls.canonical_state_topic(device)
            if canonical_topic != topic:
                cls._update_device_state(
                    canonical_topic,
                    cls._normalize_device_payload(payload, device=device, topic=topic),
                )
        cls.refresh_all_step_results()
        return previous

    @staticmethod
    def _update_device_state(topic, payload):
        flattened = flatten_payload(payload)
        previous = {s.key: s.value for s in DeviceState.objects.filter(topic=topic, key__in=flattened.keys())}
        for key, value in flattened.items():
            DeviceState.objects.update_or_create(topic=topic, key=key, defaults={"value": value})
        return previous

    @staticmethod
    def _changed_keys(payload, previous, *, require_previous):
        changed = set()
        for key, current in flatten_payload(payload).items():
            # ``None`` is the initialized/unknown sentinel for canonical state
            # rows.  With require_previous=True it must behave like no prior
            # observation, preserving the old first-event suppression semantics.
            if key not in previous or previous[key] is None:
                if not require_previous:
                    changed.add(key)
            elif previous[key] != current:
                changed.add(key)
        return changed

    @staticmethod
    def _event_id(payload):
        return str(payload["event_id"]) if isinstance(payload, dict) and payload.get("event_id") else uuid.uuid4().hex

    @classmethod
    def _resolve_device_for_topic(cls, topic):
        topic = str(topic or "")
        canonical_prefix = f"{CANONICAL_STATE_PREFIX}/"
        if topic.startswith(canonical_prefix) and topic.endswith("/state"):
            uid = topic[len(canonical_prefix):-len("/state")]
            if uid and "/" not in uid:
                return Device.objects.filter(device_uid=uid).first()

        prefix = "zigbee2mqtt/"
        if topic.startswith(prefix):
            remainder = topic[len(prefix):]
            uid, separator, suffix = remainder.partition("/")
            if uid and (not separator or suffix == "availability"):
                return Device.objects.filter(device_uid=uid).first()
        return None

    @classmethod
    def _normalize_device_payload(cls, payload, *, device=None, topic=None):
        if not isinstance(payload, dict):
            payload = {"value": payload}

        normalized = dict(payload)
        topic = str(topic or "")

        # Zigbee2MQTT availability uses a dedicated subtopic and reports
        # {"state": "online"|"offline"}.  Do not copy that value into the
        # canonical cover/light `state` key; expose it as the generic online
        # boolean instead.
        if topic.endswith("/availability"):
            availability = normalized.get("state", normalized.get("value"))
            if isinstance(availability, str):
                availability = availability.strip().lower()
                if availability in {"online", "offline"}:
                    return {"online": availability == "online"}
            return {}

        state = normalized.get("state")
        if "power" not in normalized and isinstance(state, str):
            if state.upper() == "ON":
                normalized["power"] = True
            elif state.upper() == "OFF":
                normalized["power"] = False

        if device is not None and device.device_type == WindowPusherService.DEVICE_TYPE:
            normalized = WindowPusherService.normalize_report(device, normalized)

        return normalized

    @classmethod
    def _device_from_config(cls, config):
        if config.get("device_id"):
            device = Device.objects.filter(pk=config["device_id"]).first()
            if device is not None:
                return device
        if config.get("device_uid"):
            return Device.objects.filter(device_uid=config["device_uid"]).first()
        return None

    @classmethod
    def _device_from_event_payload(cls, payload):
        if payload.get("device_id"):
            return Device.objects.filter(pk=payload["device_id"]).first()
        if payload.get("device_uid"):
            return Device.objects.filter(device_uid=payload["device_uid"]).first()
        return None

    @classmethod
    def _device_trigger_was_affected(cls, config, *, topic, device, device_changed_keys, trigger_payload):
        configured = cls._device_from_config(config)
        if configured is not None:
            if device is None or configured.pk != device.pk:
                return False
            changed = set(device_changed_keys or [])
        else:
            state_topic = str(config.get("topic") or "")
            if not state_topic or state_topic != topic:
                return False
            changed = set(trigger_payload.get("changed_keys") or [])
        key = str(config.get("key") or "")
        return bool(changed) if key == "*" else bool(key) and key in changed

    @classmethod
    def _trigger_previous_value(cls, *, config, device, state_topic, key, trigger_payload):
        if device is not None and (trigger_payload.get("device_id") == device.pk or trigger_payload.get("device_uid") == device.device_uid):
            return (trigger_payload.get("device_previous") or {}).get(key, _MISSING)
        if trigger_payload.get("topic") == state_topic:
            return (trigger_payload.get("previous") or {}).get(key, _MISSING)
        return _MISSING


SchedulerService = AutomationService

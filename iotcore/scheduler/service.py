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
from .calculator import (
    calculate_next_run,
    calculate_next_schedule,
    is_schedule_window,
    schedule_window_matches,
)
from .constants import MATCHED_ACTION_IDS_KEY


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
    def _schedule_condition(cls, trigger):
        if trigger.trigger_type != AutomationTrigger.TriggerType.SET:
            return None
        return (
            trigger.conditions
            .filter(condition_type=AutomationCondition.ConditionType.SCHEDULE)
            .order_by("order", "id")
            .first()
        )

    @classmethod
    def recalculate_trigger(cls, trigger, after=None):
        previous_next = trigger.next_run_at
        trigger.next_run_at = None
        if trigger.enabled and trigger.automation.enabled:
            if trigger.trigger_type == AutomationTrigger.TriggerType.SET:
                schedule_condition = cls._schedule_condition(trigger)
                if schedule_condition is not None:
                    trigger.next_run_at = calculate_next_schedule(
                        schedule_condition.config or {},
                        after=after,
                        previous_next=previous_next,
                    )
            elif trigger.trigger_type == AutomationTrigger.TriggerType.TIME:
                trigger.next_run_at = calculate_next_schedule(
                    trigger.config or {},
                    after=after,
                    previous_next=previous_next,
                )
                # Legacy one-shot triggers used to disable themselves when the
                # schedule expired. Current SET rows remain enabled because the
                # set may have other event sources besides its schedule.
                if trigger.next_run_at is None:
                    trigger.enabled = False
        trigger.save(update_fields=["enabled", "next_run_at", "updated_at"])
        return trigger.next_run_at

    @classmethod
    def recalculate_automation(cls, automation, after=None):
        for trigger in automation.triggers.select_related("automation"):
            cls.recalculate_trigger(trigger, after=after)
            if trigger.trigger_type == AutomationTrigger.TriggerType.SET:
                # Re-enabling an automation must re-arm each set from the
                # current state instead of keeping a stale truth value from
                # the period while the automation was disabled.
                cls.refresh_trigger_result(trigger, now=after)

    @classmethod
    def refresh_trigger_result(cls, trigger, now=None):
        """Synchronize a set's resting truth value without executing it.

        This prevents a newly saved set whose persistent conditions are already
        true from firing merely because the next unrelated state report
        arrives. Exact reservation times, MQTT events, and changed operators
        are transient while weekly time ranges remain persistent constraints.
        """
        if trigger.trigger_type != AutomationTrigger.TriggerType.SET:
            return False
        now = now or timezone.now()
        conditions = list(trigger.conditions.order_by("order", "id"))
        result = cls._condition_list_matches(
            conditions,
            now,
            trigger_payload={},
            condition_operator=trigger.condition_operator,
            resting=True,
            empty_matches=False,
        )
        if trigger.last_result != result:
            trigger.last_result = result
            trigger.save(update_fields=["last_result", "updated_at"])
        return result

    @classmethod
    def refresh_all_trigger_results(cls, now=None):
        """Synchronize all active SET rows without executing actions.

        Retained MQTT/state synchronization uses this to ensure a deployed or
        restarted process does not treat an already-true condition as a fresh
        FALSE -> TRUE edge on the first live report.
        """
        now = now or timezone.now()
        for trigger in (
            AutomationTrigger.objects
            .filter(
                trigger_type=AutomationTrigger.TriggerType.SET,
                enabled=True,
                automation__enabled=True,
            )
            .select_related("automation")
        ):
            cls.refresh_trigger_result(trigger, now=now)

    @classmethod
    def enqueue_due(cls, now=None):
        now = now or timezone.now()
        enqueued = []
        due_ids = list(
            AutomationTrigger.objects.filter(
                trigger_type__in=[
                    AutomationTrigger.TriggerType.SET,
                    AutomationTrigger.TriggerType.TIME,
                ],
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
                if trigger.trigger_type == AutomationTrigger.TriggerType.SET:
                    schedule_condition = cls._schedule_condition(trigger)
                    if schedule_condition is None:
                        trigger.next_run_at = None
                        trigger.save(update_fields=["next_run_at", "updated_at"])
                        continue
                    automation_run = cls._enqueue_locked(
                        trigger,
                        now=now,
                        scheduled_for=scheduled_for,
                        trigger_payload={
                            "trigger_id": trigger.id,
                            "type": "schedule",
                            "source_condition_id": schedule_condition.id,
                        },
                    )
                    trigger.next_run_at = calculate_next_schedule(
                        schedule_condition.config or {},
                        after=now,
                        previous_next=scheduled_for,
                    )
                    trigger.save(update_fields=["next_run_at", "updated_at"])
                else:
                    # Legacy TIME trigger path.
                    automation_run = cls._enqueue_locked(
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
                    trigger.save(update_fields=[
                        "last_triggered_at", "next_run_at", "enabled", "updated_at"
                    ])

                if automation_run is not None:
                    enqueued.append(automation_run)
        return enqueued

    @classmethod
    def process_event(cls, topic, payload, now=None):
        """Process a live MQTT message and re-evaluate affected trigger sets."""
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
        device_changed_keys = (
            cls._changed_keys(
                normalized_payload,
                previous_device,
                require_previous=True,
            )
            if device is not None else set()
        )

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
        """Update canonical last-known state and evaluate affected sets."""
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
                    AutomationTrigger.TriggerType.SET,
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

                if trigger.trigger_type == AutomationTrigger.TriggerType.SET:
                    if not cls._set_has_event_source(
                        trigger,
                        topic=topic,
                        device=device,
                        device_changed_keys=device_changed_keys,
                        trigger_payload=trigger_payload,
                    ):
                        continue
                else:
                    # Legacy trigger compatibility.
                    config = trigger.config or {}
                    if trigger.trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
                        if not mqtt.topic_matches_sub(str(config.get("topic", "")), topic):
                            continue
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
                    enqueued.append(automation_run)
        return enqueued

    @classmethod
    def _set_has_event_source(
        cls,
        trigger,
        *,
        topic,
        device,
        device_changed_keys,
        trigger_payload,
    ):
        for condition in trigger.conditions.order_by("order", "id"):
            config = condition.config or {}
            if condition.condition_type == AutomationCondition.ConditionType.MQTT_EVENT:
                pattern = str(config.get("topic") or "")
                if pattern and mqtt.topic_matches_sub(pattern, topic):
                    return True
            elif condition.condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
                if cls._device_condition_was_affected(
                    config,
                    topic=topic,
                    device=device,
                    device_changed_keys=device_changed_keys,
                    trigger_payload=trigger_payload,
                ):
                    return True
            elif condition.condition_type == AutomationCondition.ConditionType.EVENT_VALUE:
                # Legacy event-value conditions have no source address of their
                # own. New data is migrated to MQTT_EVENT, so avoid waking a
                # set on every unrelated MQTT message here.
                continue
        return False

    @classmethod
    def _device_condition_was_affected(
        cls,
        config,
        *,
        topic,
        device,
        device_changed_keys,
        trigger_payload,
    ):
        configured_device = cls._device_from_config(config)
        if configured_device is not None:
            if device is None or configured_device.pk != device.pk:
                return False
            changed = set(device_changed_keys or [])
        else:
            state_topic = str(config.get("topic") or "")
            if not state_topic or state_topic != topic:
                return False
            changed = set(trigger_payload.get("changed_keys") or [])

        key = str(config.get("key") or "")
        if key == "*":
            return bool(changed)
        return bool(key) and key in changed

    @classmethod
    def _enqueue_locked(
        cls,
        trigger,
        now,
        scheduled_for=None,
        source_event_id=None,
        trigger_payload=None,
    ):
        if trigger.trigger_type == AutomationTrigger.TriggerType.SET:
            return cls._enqueue_set_locked(
                trigger,
                now=now,
                scheduled_for=scheduled_for,
                source_event_id=source_event_id,
                trigger_payload=trigger_payload,
            )
        return cls._enqueue_legacy_locked(
            trigger,
            now=now,
            scheduled_for=scheduled_for,
            source_event_id=source_event_id,
            trigger_payload=trigger_payload,
        )

    @classmethod
    def _enqueue_set_locked(
        cls,
        trigger,
        *,
        now,
        scheduled_for=None,
        source_event_id=None,
        trigger_payload=None,
    ):
        automation = Automation.objects.select_for_update().get(pk=trigger.automation_id)
        if not automation.enabled or not trigger.enabled:
            return None

        actions = list(trigger.actions.order_by("order", "id"))
        conditions = list(trigger.conditions.order_by("order", "id"))
        if not actions or not conditions:
            return None

        previous_result = bool(trigger.last_result)
        current_result = cls._condition_list_matches(
            conditions,
            now,
            trigger_payload=trigger_payload,
            condition_operator=trigger.condition_operator,
            resting=False,
            empty_matches=False,
        )
        resting_result = cls._condition_list_matches(
            conditions,
            now,
            trigger_payload=trigger_payload,
            condition_operator=trigger.condition_operator,
            resting=True,
            empty_matches=False,
        )

        def save_resting_result():
            if trigger.last_result != resting_result:
                trigger.last_result = resting_result
                trigger.save(update_fields=["last_result", "updated_at"])

        # Trigger-set semantics: execute only on FALSE -> TRUE. The stored
        # value is the truth value after transient event conditions subside.
        if not current_result or previous_result:
            save_resting_result()
            return None

        if (
            automation.cooldown_seconds
            and trigger.last_triggered_at
            and (now - trigger.last_triggered_at).total_seconds()
            < automation.cooldown_seconds
        ):
            save_resting_result()
            return None

        run_payload = dict(trigger_payload or {})
        run_payload["trigger_id"] = trigger.pk
        run_payload[MATCHED_ACTION_IDS_KEY] = [action.pk for action in actions]

        if scheduled_for is not None:
            automation_run, created = AutomationRun.objects.get_or_create(
                trigger=trigger,
                scheduled_for=scheduled_for,
                defaults={
                    "automation": automation,
                    "trigger_payload": run_payload,
                },
            )
        else:
            automation_run, created = AutomationRun.objects.get_or_create(
                trigger=trigger,
                source_event_id=source_event_id,
                defaults={
                    "automation": automation,
                    "trigger_payload": run_payload,
                },
            )
        if not created:
            save_resting_result()
            return None

        trigger.last_result = resting_result
        trigger.last_triggered_at = now
        trigger.save(update_fields=["last_result", "last_triggered_at", "updated_at"])
        automation.last_triggered_at = now
        automation.save(update_fields=["last_triggered_at", "updated_at"])
        return automation_run

    @classmethod
    def _enqueue_legacy_locked(
        cls,
        trigger,
        *,
        now,
        scheduled_for=None,
        source_event_id=None,
        trigger_payload=None,
    ):
        """Pre-0020 execution semantics retained for compatibility/tests."""
        automation = Automation.objects.select_for_update().get(pk=trigger.automation_id)
        if not automation.enabled:
            return None

        action = trigger.actions.order_by("order", "id").first()

        cooldown_anchor = trigger.last_triggered_at if action is not None else automation.last_triggered_at
        if (
            automation.cooldown_seconds
            and cooldown_anchor
            and (now - cooldown_anchor).total_seconds() < automation.cooldown_seconds
        ):
            return None

        run_payload = dict(trigger_payload or {})
        run_payload["trigger_id"] = trigger.pk
        if action is not None:
            conditions = list(
                action.conditions
                .filter(automation_id=automation.pk)
                .order_by("order", "id")
            )
            if not cls._condition_list_matches(
                conditions,
                now,
                trigger_payload=trigger_payload,
            ):
                return None
            run_payload[MATCHED_ACTION_IDS_KEY] = [action.pk]
        else:
            matched_action_ids = cls._matching_action_ids(
                automation,
                now,
                trigger_payload=trigger_payload,
            )
            if matched_action_ids == []:
                return None
            if matched_action_ids is None:
                if not cls._conditions_match(
                    automation,
                    now,
                    trigger_payload=trigger_payload,
                ):
                    return None
            else:
                run_payload[MATCHED_ACTION_IDS_KEY] = matched_action_ids

        if scheduled_for is not None:
            if action is None:
                existing = AutomationRun.objects.filter(
                    automation=automation,
                    scheduled_for=scheduled_for,
                ).first()
                if existing is not None:
                    return None
            automation_run, created = AutomationRun.objects.get_or_create(
                trigger=trigger,
                scheduled_for=scheduled_for,
                defaults={"automation": automation, "trigger_payload": run_payload},
            )
        else:
            if action is None:
                existing = AutomationRun.objects.filter(
                    automation=automation,
                    source_event_id=source_event_id,
                ).first()
                if existing is not None:
                    return None
            automation_run, created = AutomationRun.objects.get_or_create(
                trigger=trigger,
                source_event_id=source_event_id,
                defaults={"automation": automation, "trigger_payload": run_payload},
            )
        if not created:
            return None

        trigger.last_triggered_at = now
        trigger.save(update_fields=["last_triggered_at", "updated_at"])
        automation.last_triggered_at = now
        automation.save(update_fields=["last_triggered_at", "updated_at"])
        return automation_run

    @classmethod
    def _matching_action_ids(cls, automation, now, trigger_payload=None):
        actions = list(
            automation.actions
            .prefetch_related("conditions")
            .order_by("order", "id")
        )
        if not actions:
            return None

        legacy_conditions = list(
            automation.conditions
            .filter(action__isnull=True, trigger__isnull=True)
            .order_by("order", "id")
        )
        matched = []
        for action in actions:
            conditions = [
                condition
                for condition in action.conditions.all()
                if condition.automation_id == automation.pk
            ]
            if legacy_conditions:
                conditions.extend(legacy_conditions)
            if cls._condition_list_matches(
                conditions,
                now,
                trigger_payload=trigger_payload,
            ):
                matched.append(action.pk)
        return matched

    @classmethod
    def _conditions_match(cls, automation, now, trigger_payload=None):
        conditions = automation.conditions.filter(
            action__isnull=True,
            trigger__isnull=True,
        ).order_by("order", "id")
        return cls._condition_list_matches(
            conditions,
            now,
            trigger_payload=trigger_payload,
        )

    @classmethod
    def _condition_list_matches(
        cls,
        conditions,
        now,
        trigger_payload=None,
        *,
        condition_operator=AutomationTrigger.ConditionOperator.AND,
        resting=False,
        empty_matches=True,
    ):
        conditions = list(conditions)
        if not conditions:
            return empty_matches
        results = [
            cls._condition_matches(
                condition,
                now,
                trigger_payload=trigger_payload,
                resting=resting,
            )
            for condition in conditions
        ]
        if condition_operator == AutomationTrigger.ConditionOperator.OR:
            return any(results)
        return all(results)

    @classmethod
    def _condition_matches(
        cls,
        condition,
        now,
        trigger_payload=None,
        *,
        resting=False,
    ):
        local_now = timezone.localtime(now)
        trigger_payload = trigger_payload or {}
        config = condition.config or {}

        if condition.condition_type == AutomationCondition.ConditionType.SCHEDULE:
            if is_schedule_window(config):
                return schedule_window_matches(config, now=now)
            if resting:
                return False
            try:
                source_condition_id = int(trigger_payload.get("source_condition_id"))
            except (TypeError, ValueError):
                return False
            return source_condition_id == condition.pk

        if condition.condition_type == AutomationCondition.ConditionType.TIME_WINDOW:
            start = parse_time(str(config.get("start", "")))
            end = parse_time(str(config.get("end", "")))
            if start is None or end is None:
                return False
            weekdays = config.get("weekdays") or list(range(7))
            if local_now.weekday() not in {int(day) for day in weekdays}:
                return False
            current = local_now.time().replace(tzinfo=None)
            return start <= current <= end if start <= end else (
                current >= start or current <= end
            )

        if condition.condition_type == AutomationCondition.ConditionType.MQTT_EVENT:
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

        if condition.condition_type == AutomationCondition.ConditionType.EVENT_VALUE:
            if resting:
                return False
            field = config.get("field") or "value"
            current = get_nested_value(trigger_payload.get("payload", {}), field)
            previous = (trigger_payload.get("previous") or {}).get(field, _MISSING)
            return compare_value(
                config.get("operator") or "eq",
                current,
                config.get("value"),
                previous,
            )

        if condition.condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
            operator = config.get("operator") or "eq"
            key = config.get("key", "")
            device = cls._device_from_config(config)
            state_topic = config.get("topic", "")
            if device is not None:
                state_topic = cls.canonical_state_topic(device)
            if not state_topic:
                return False

            # changed/changed_to are transient edge conditions. A wildcard key
            # is used only by the migration to represent legacy "any state of
            # this device changed" triggers without reintroducing a separate
            # watcher field in the UI.
            if operator in {"changed", "changed_to"} and resting:
                return False
            if key == "*":
                if operator != "changed":
                    return False
                return cls._device_condition_was_affected(
                    config,
                    topic=str(trigger_payload.get("topic") or ""),
                    device=cls._device_from_event_payload(trigger_payload),
                    device_changed_keys=set(trigger_payload.get("device_changed_keys") or []),
                    trigger_payload=trigger_payload,
                )

            state = DeviceState.objects.filter(topic=state_topic, key=key).first()
            current = state.value if state is not None else _MISSING
            previous = cls._condition_previous_value(
                config=config,
                device=device,
                state_topic=state_topic,
                key=key,
                trigger_payload=trigger_payload,
            )
            return compare_value(operator, current, config.get("value"), previous)

        return False

    @classmethod
    def _device_from_event_payload(cls, trigger_payload):
        device_id = trigger_payload.get("device_id")
        if device_id:
            return Device.objects.filter(pk=device_id).first()
        device_uid = trigger_payload.get("device_uid")
        if device_uid:
            return Device.objects.filter(device_uid=device_uid).first()
        return None

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
        # Retained/current-state synchronization is intentionally non-firing,
        # but it must still keep SET edge state aligned with the state cache.
        cls.refresh_all_trigger_results()
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
            return (trigger_payload.get("previous") or {}).get(key, _MISSING)
        return _MISSING


# Backward-compatible import for the existing management command name.
SchedulerService = AutomationService

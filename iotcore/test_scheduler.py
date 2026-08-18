import json
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .device.services.device_service import DeviceService
from .device.services.sequence_executor import SequenceExecutor
from .forms import (
    AutomationActionForm,
    AutomationConditionForm,
    AutomationConditionFormSet,
    AutomationTriggerForm,
    AutomationTriggerFormSet,
)
from .models import (
    Automation,
    AutomationAction,
    AutomationCondition,
    AutomationRun,
    AutomationTrigger,
    Device,
    DeviceState,
    Sequence,
    SequenceRun,
    SequenceStep,
    SequenceStepRun,
)
from .scheduler.calculator import calculate_next_run, describe_trigger
from .scheduler.executor import AutomationExecutor
from .scheduler.constants import MATCHED_ACTION_IDS_KEY
from .scheduler.service import AutomationService


class AutomationCalculatorTests(TestCase):
    def setUp(self):
        self.sequence = Sequence.objects.create(name="테스트 시퀀스")
        self.automation = Automation.objects.create(name="시간 테스트")

    def make_trigger(self, config):
        return AutomationTrigger(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.TIME,
            config=config,
        )

    def test_daily_trigger_uses_next_local_time(self):
        trigger = self.make_trigger({
            "schedule_type": AutomationTrigger.ScheduleType.DAILY,
            "time": "08:30",
        })
        after = timezone.make_aware(datetime(2026, 8, 1, 9, 0))

        next_run = timezone.localtime(calculate_next_run(trigger, after=after))

        self.assertEqual(next_run.date().isoformat(), "2026-08-02")
        self.assertEqual(next_run.time().replace(tzinfo=None), time(8, 30))

    def test_weekly_trigger_selects_enabled_weekday(self):
        trigger = self.make_trigger({
            "schedule_type": AutomationTrigger.ScheduleType.WEEKLY,
            "time": "07:00",
            "weekdays": [0],
        })
        after = timezone.make_aware(datetime(2026, 8, 1, 9, 0))

        next_run = timezone.localtime(calculate_next_run(trigger, after=after))

        self.assertEqual(next_run.date().isoformat(), "2026-08-03")
        self.assertEqual(next_run.weekday(), 0)

    def test_interval_trigger_advances_from_existing_next_run(self):
        after = timezone.now()
        trigger = self.make_trigger({
            "schedule_type": AutomationTrigger.ScheduleType.INTERVAL,
            "every": 5,
            "unit": "minutes",
        })
        trigger.next_run_at = after - timedelta(minutes=11)

        next_run = calculate_next_run(trigger, after=after)

        self.assertGreater(next_run, after)
        self.assertLessEqual(next_run, after + timedelta(minutes=5))

    def test_trigger_summary_uses_korean_12_hour_clock(self):
        morning = self.make_trigger({
            "schedule_type": AutomationTrigger.ScheduleType.DAILY,
            "time": "08:00",
        })
        evening = self.make_trigger({
            "schedule_type": AutomationTrigger.ScheduleType.DAILY,
            "time": "18:30",
        })
        once = self.make_trigger({
            "schedule_type": AutomationTrigger.ScheduleType.ONCE,
            "run_at": "2026-08-05T22:15:00+09:00",
        })

        self.assertEqual(describe_trigger(morning), "매일 오전 8:00")
        self.assertEqual(describe_trigger(evening), "매일 오후 6:30")
        self.assertEqual(
            describe_trigger(once),
            "2026-08-05 오후 10:15 한 번",
        )

    def test_all_weekdays_are_displayed_as_daily(self):
        trigger = self.make_trigger({
            "schedule_type": AutomationTrigger.ScheduleType.WEEKLY,
            "time": "08:00",
            "weekdays": list(range(7)),
        })

        self.assertEqual(describe_trigger(trigger), "매일 오전 8:00")


class AutomationTriggerFormTests(TestCase):
    def test_mqtt_trigger_only_stores_broad_topic(self):
        form = AutomationTriggerForm(data={
            "trigger_type": AutomationTrigger.TriggerType.MQTT_EVENT,
            "enabled": "on",
            "event_topic": "zigbee2mqtt/front_door",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["config"],
            {"topic": "zigbee2mqtt/front_door"},
        )

    def test_hidden_time_fields_do_not_leak_into_mqtt_config(self):
        form = AutomationTriggerForm(data={
            "trigger_type": AutomationTrigger.TriggerType.MQTT_EVENT,
            "enabled": "on",
            "schedule_type": AutomationTrigger.ScheduleType.WEEKLY,
            "time_of_day": "08:30",
            "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
            "event_topic": "zigbee2mqtt/front_door",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["config"],
            {"topic": "zigbee2mqtt/front_door"},
        )

    def test_existing_legacy_mqtt_filter_is_preserved_on_save(self):
        automation = Automation.objects.create(name="기존 MQTT 예약 실행")
        trigger = AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config={
                "topic": "zigbee2mqtt/front_door",
                "field": "contact",
                "operator": "changed_to",
                "value": False,
            },
        )
        legacy_config = {
            "field": "contact",
            "operator": "changed_to",
            "value": False,
        }
        form = AutomationTriggerForm(
            data={
                "trigger_type": AutomationTrigger.TriggerType.MQTT_EVENT,
                "enabled": "on",
                "event_topic": "zigbee2mqtt/front_door",
                "legacy_event_config": json.dumps(legacy_config),
            },
            instance=trigger,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["config"],
            {"topic": "zigbee2mqtt/front_door", **legacy_config},
        )

    def test_device_state_trigger_stores_iotcore_device_identity(self):
        sensor = Device.objects.create(
            device_uid="room-sensor",
            name="방 센서",
            device_type="sensor",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        form = AutomationTriggerForm(data={
            "trigger_type": AutomationTrigger.TriggerType.DEVICE_STATE,
            "enabled": "on",
            "state_device": sensor.pk,
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["config"]["device_id"], sensor.pk)
        self.assertEqual(
            form.cleaned_data["config"]["device_uid"],
            "room-sensor",
        )


class AutomationActionFormTests(TestCase):
    def test_rejects_action_not_supported_by_selected_device(self):
        light = Device.objects.create(
            device_uid="form-light",
            name="폼 테스트 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        form = AutomationActionForm(data={
            "action_type": AutomationAction.ActionType.DEVICE,
            "device": light.pk,
            "function": "ytmusic.refresh_cookie",
            "delay": 0,
        })

        self.assertFalse(form.is_valid())
        self.assertIn("function", form.errors)


class AutomationConditionFormTests(TestCase):
    def test_empty_optional_condition_does_not_raise_required_error(self):
        form = AutomationConditionForm(data={"condition_type": ""})

        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_time_window_condition_is_ignored(self):
        form = AutomationConditionForm(data={"condition_type": "time_window"})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["condition_type"])

    def test_device_state_condition_parses_numeric_comparison(self):
        sensor = Device.objects.create(
            device_uid="condition-sensor",
            name="조건 센서",
            device_type="sensor",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        form = AutomationConditionForm(data={
            "condition_type": AutomationCondition.ConditionType.DEVICE_STATE,
            "state_device": sensor.pk,
            "state_key": "temperature",
            "state_operator": "gte",
            "state_value": "28",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["config"]["operator"], "gte")
        self.assertEqual(form.cleaned_data["config"]["value"], 28)

    def test_unresolved_legacy_device_state_topic_is_preserved_on_save(self):
        automation = Automation.objects.create(name="기존 상태 조건")
        condition = AutomationCondition.objects.create(
            automation=automation,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            order=1,
            config={
                "topic": "custom/sensor/state",
                "key": "temperature",
                "operator": "gte",
                "value": 28,
            },
        )
        form = AutomationConditionForm(
            data={
                "condition_type": AutomationCondition.ConditionType.DEVICE_STATE,
                "state_device": "",
                "state_key": "temperature",
                "state_operator": "gte",
                "state_value": "28",
            },
            instance=condition,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["config"],
            {
                "topic": "custom/sensor/state",
                "key": "temperature",
                "operator": "gte",
                "value": 28,
            },
        )

    def test_event_value_condition_supports_changed_without_value(self):
        form = AutomationConditionForm(data={
            "condition_type": AutomationCondition.ConditionType.EVENT_VALUE,
            "event_field": "contact",
            "event_operator": "changed",
            "event_value": "",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["config"]["operator"], "changed")
        self.assertIsNone(form.cleaned_data["config"]["value"])


class AutomationOptionalFormSetTests(TestCase):
    def test_incomplete_extra_trigger_does_not_count_as_active(self):
        data = {
            "triggers-TOTAL_FORMS": "2",
            "triggers-INITIAL_FORMS": "0",
            "triggers-MIN_NUM_FORMS": "0",
            "triggers-MAX_NUM_FORMS": "1000",
            "triggers-0-trigger_type": "time",
            "triggers-0-schedule_type": "weekly",
            "triggers-0-time_of_day": "08:00",
            "triggers-0-weekdays": ["0", "1", "2", "3", "4", "5", "6"],
            "triggers-1-trigger_type": "time",
        }
        formset = AutomationTriggerFormSet(
            data,
            instance=Automation(),
            prefix="triggers",
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertIsNone(formset.forms[1].cleaned_data["trigger_type"])


class AutomationServiceTests(TestCase):
    def setUp(self):
        self.sequence = Sequence.objects.create(name="예약 시퀀스")
        self.automation = Automation.objects.create(name="예약 실행")

    def test_due_once_trigger_is_enqueued_and_disabled(self):
        now = timezone.now()
        scheduled_for = now - timedelta(seconds=1)
        trigger = AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.TIME,
            config={
                "schedule_type": AutomationTrigger.ScheduleType.ONCE,
                "run_at": scheduled_for.isoformat(),
            },
            next_run_at=scheduled_for,
        )

        runs = AutomationService.enqueue_due(now=now)

        self.assertEqual(len(runs), 1)
        run = AutomationRun.objects.get()
        self.assertEqual(run.status, AutomationRun.Status.PENDING)
        trigger.refresh_from_db()
        self.assertFalse(trigger.enabled)
        self.assertIsNone(trigger.next_run_at)

    def test_same_time_trigger_is_not_enqueued_twice(self):
        now = timezone.now()
        scheduled_for = now - timedelta(seconds=1)
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.TIME,
            config={
                "schedule_type": AutomationTrigger.ScheduleType.INTERVAL,
                "every": 5,
                "unit": "minutes",
            },
            next_run_at=scheduled_for,
        )

        first = AutomationService.enqueue_due(now=now)
        second = AutomationService.enqueue_due(now=now)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(AutomationRun.objects.count(), 1)

    def test_same_mqtt_event_id_is_deduplicated_per_automation(self):
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config={
                "topic": "zigbee2mqtt/front_door",
                "field": "contact",
                "operator": "eq",
                "value": False,
            },
        )
        payload = {"event_id": "door-event-1", "contact": False}

        first = AutomationService.process_event(
            "zigbee2mqtt/front_door",
            payload,
        )
        second = AutomationService.process_event(
            "zigbee2mqtt/front_door",
            payload,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(AutomationRun.objects.count(), 1)

    def test_multiple_matching_triggers_only_enqueue_once(self):
        config = {
            "topic": "zigbee2mqtt/front_door",
            "field": "contact",
            "operator": "eq",
            "value": False,
        }
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config=config,
        )
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config=config,
        )

        runs = AutomationService.process_event(
            "zigbee2mqtt/front_door",
            {"contact": False},
        )

        self.assertEqual(len(runs), 1)
        self.assertEqual(AutomationRun.objects.count(), 1)

    def test_changed_to_uses_previous_device_state(self):
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config={
                "topic": "zigbee2mqtt/front_door",
                "field": "contact",
                "operator": "changed_to",
                "value": False,
            },
        )
        AutomationService.update_device_state(
            "zigbee2mqtt/front_door",
            {"contact": True},
        )

        runs = AutomationService.process_event(
            "zigbee2mqtt/front_door",
            {"contact": False},
        )

        self.assertEqual(len(runs), 1)

    def test_mqtt_trigger_uses_event_value_condition_for_filtering(self):
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config={"topic": "zigbee2mqtt/front_door"},
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            condition_type=AutomationCondition.ConditionType.EVENT_VALUE,
            order=1,
            config={
                "field": "contact",
                "operator": "eq",
                "value": False,
            },
        )

        ignored = AutomationService.process_event(
            "zigbee2mqtt/front_door",
            {"contact": True},
        )
        matched = AutomationService.process_event(
            "zigbee2mqtt/front_door",
            {"contact": False},
        )

        self.assertEqual(ignored, [])
        self.assertEqual(len(matched), 1)

    def test_device_state_trigger_fires_only_after_known_state_changes(self):
        sensor = Device.objects.create(
            device_uid="room-temp",
            name="방 온습도",
            device_type="sensor",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.DEVICE_STATE,
            config={
                "device_id": sensor.pk,
                "device_uid": sensor.device_uid,
                "device_name": sensor.name,
            },
        )
        AutomationService.update_device_state(
            "zigbee2mqtt/room-temp",
            {"temperature": 27.0},
        )

        unchanged = AutomationService.process_event(
            "zigbee2mqtt/room-temp",
            {"temperature": 27.0},
        )
        changed = AutomationService.process_event(
            "zigbee2mqtt/room-temp",
            {"temperature": 28.0},
        )

        self.assertEqual(unchanged, [])
        self.assertEqual(len(changed), 1)

    def test_device_state_condition_reads_canonical_state_and_numeric_operator(self):
        sensor = Device.objects.create(
            device_uid="temperature-condition",
            name="온도 센서",
            device_type="sensor",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            order=1,
            config={
                "device_id": sensor.pk,
                "device_uid": sensor.device_uid,
                "key": "temperature",
                "operator": "gte",
                "value": 28,
            },
        )
        AutomationService.update_device_state(
            "zigbee2mqtt/temperature-condition",
            {"temperature": 28.4},
        )

        self.assertTrue(
            AutomationService._conditions_match(self.automation, timezone.now())
        )

    def test_record_device_state_can_drive_device_change_trigger(self):
        light = Device.objects.create(
            device_uid="state-light",
            name="상태 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.DEVICE_STATE,
            config={
                "device_id": light.pk,
                "device_uid": light.device_uid,
                "device_name": light.name,
            },
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            order=1,
            config={
                "device_id": light.pk,
                "device_uid": light.device_uid,
                "key": "power",
                "operator": "changed_to",
                "value": False,
            },
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(light),
            {"power": True},
        )

        runs = AutomationService.record_device_state(
            light,
            {"power": False},
        )

        self.assertEqual(len(runs), 1)

    def test_time_window_condition_supports_crossing_midnight(self):
        AutomationCondition.objects.create(
            automation=self.automation,
            condition_type=AutomationCondition.ConditionType.TIME_WINDOW,
            config={"start": "22:00", "end": "06:00"},
        )
        now = timezone.make_aware(datetime(2026, 8, 2, 1, 0))

        self.assertTrue(
            AutomationService._conditions_match(self.automation, now)
        )

    def test_action_conditions_are_evaluated_independently(self):
        light = Device.objects.create(
            device_uid="rule-light",
            name="규칙 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        aircon = Device.objects.create(
            device_uid="rule-aircon",
            name="규칙 에어컨",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="방",
        )
        light_action = AutomationAction.objects.create(
            automation=self.automation,
            order=1,
            action_type=AutomationAction.ActionType.DEVICE,
            device=light,
            function="power_off",
        )
        aircon_action = AutomationAction.objects.create(
            automation=self.automation,
            order=2,
            action_type=AutomationAction.ActionType.DEVICE,
            device=aircon,
            function="power_off",
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            action=light_action,
            order=1,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            config={
                "device_id": light.pk,
                "device_uid": light.device_uid,
                "key": "power",
                "operator": "eq",
                "value": True,
            },
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            action=aircon_action,
            order=1,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            config={
                "device_id": aircon.pk,
                "device_uid": aircon.device_uid,
                "key": "power",
                "operator": "eq",
                "value": True,
            },
        )
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config={"topic": "iotcore/test/rule"},
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(light),
            {"power": False},
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(aircon),
            {"power": True},
        )

        runs = AutomationService.process_event(
            "iotcore/test/rule",
            {"event_id": "independent-rules-1", "value": 1},
        )

        self.assertEqual(len(runs), 1)
        self.assertEqual(
            runs[0].trigger_payload[MATCHED_ACTION_IDS_KEY],
            [aircon_action.pk],
        )

    def test_multiple_conditions_inside_one_action_use_and(self):
        aircon = Device.objects.create(
            device_uid="and-aircon",
            name="AND 에어컨",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="방",
        )
        action = AutomationAction.objects.create(
            automation=self.automation,
            order=1,
            action_type=AutomationAction.ActionType.DEVICE,
            device=aircon,
            function="power_off",
        )
        for order, key, value in [
            (1, "power", True),
            (2, "mode", "fan"),
        ]:
            AutomationCondition.objects.create(
                automation=self.automation,
                action=action,
                order=order,
                condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
                config={
                    "device_id": aircon.pk,
                    "device_uid": aircon.device_uid,
                    "key": key,
                    "operator": "eq",
                    "value": value,
                },
            )
        AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config={"topic": "iotcore/test/and"},
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(aircon),
            {"power": True, "mode": "cool"},
        )

        ignored = AutomationService.process_event(
            "iotcore/test/and",
            {"event_id": "and-rules-1", "value": 1},
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(aircon),
            {"mode": "fan"},
        )
        matched = AutomationService.process_event(
            "iotcore/test/and",
            {"event_id": "and-rules-2", "value": 1},
        )

        self.assertEqual(ignored, [])
        self.assertEqual(len(matched), 1)
        self.assertEqual(
            matched[0].trigger_payload[MATCHED_ACTION_IDS_KEY],
            [action.pk],
        )

    def test_trigger_set_and_runs_only_on_false_to_true_and_rearms(self):
        sensor = Device.objects.create(
            device_uid="set-and-temp",
            name="AND 온도 센서",
            device_type="sensor",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        aircon = Device.objects.create(
            device_uid="set-and-aircon",
            name="AND 에어컨",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="방",
        )
        trigger = AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.SET,
            condition_operator=AutomationTrigger.ConditionOperator.AND,
        )
        AutomationAction.objects.create(
            automation=self.automation,
            trigger=trigger,
            order=1,
            action_type=AutomationAction.ActionType.DEVICE,
            device=aircon,
            function="power_off",
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            trigger=trigger,
            order=1,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            config={
                "device_id": sensor.pk,
                "device_uid": sensor.device_uid,
                "key": "temperature",
                "operator": "lt",
                "value": 24,
            },
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            trigger=trigger,
            order=2,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            config={
                "device_id": aircon.pk,
                "device_uid": aircon.device_uid,
                "key": "power",
                "operator": "eq",
                "value": True,
            },
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(sensor),
            {"temperature": 25},
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(aircon),
            {"power": True},
        )
        AutomationService.refresh_trigger_result(trigger)

        first = AutomationService.record_device_state(sensor, {"temperature": 23})
        still_true = AutomationService.record_device_state(sensor, {"temperature": 22})
        rearm = AutomationService.record_device_state(aircon, {"power": False})
        second = AutomationService.record_device_state(aircon, {"power": True})

        self.assertEqual(len(first), 1)
        self.assertEqual(still_true, [])
        self.assertEqual(rearm, [])
        self.assertEqual(len(second), 1)
        self.assertEqual(AutomationRun.objects.filter(trigger=trigger).count(), 2)

    def test_trigger_set_or_runs_once_until_all_conditions_are_false(self):
        first_device = Device.objects.create(
            device_uid="set-or-first",
            name="OR 조건 1",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        second_device = Device.objects.create(
            device_uid="set-or-second",
            name="OR 조건 2",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        trigger = AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.SET,
            condition_operator=AutomationTrigger.ConditionOperator.OR,
        )
        AutomationAction.objects.create(
            automation=self.automation,
            trigger=trigger,
            order=1,
            action_type=AutomationAction.ActionType.SEQUENCE,
            sequence=self.sequence,
        )
        for order, device in enumerate((first_device, second_device), start=1):
            AutomationCondition.objects.create(
                automation=self.automation,
                trigger=trigger,
                order=order,
                condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
                config={
                    "device_id": device.pk,
                    "device_uid": device.device_uid,
                    "key": "power",
                    "operator": "eq",
                    "value": True,
                },
            )
            AutomationService.update_device_state(
                AutomationService.canonical_state_topic(device),
                {"power": False},
            )
        AutomationService.refresh_trigger_result(trigger)

        first = AutomationService.record_device_state(first_device, {"power": True})
        second_becomes_true = AutomationService.record_device_state(second_device, {"power": True})
        first_becomes_false = AutomationService.record_device_state(first_device, {"power": False})
        all_false = AutomationService.record_device_state(second_device, {"power": False})
        after_rearm = AutomationService.record_device_state(first_device, {"power": True})

        self.assertEqual(len(first), 1)
        self.assertEqual(second_becomes_true, [])
        self.assertEqual(first_becomes_false, [])
        self.assertEqual(all_false, [])
        self.assertEqual(len(after_rearm), 1)
        self.assertEqual(AutomationRun.objects.filter(trigger=trigger).count(), 2)

    def test_device_state_condition_only_wakes_for_its_own_key(self):
        sensor = Device.objects.create(
            device_uid="key-scoped-sensor",
            name="키 범위 온습도 센서",
            device_type="sensor",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        AutomationService.update_device_state(
            AutomationService.canonical_state_topic(sensor),
            {"temperature": 23, "humidity": 50},
        )
        trigger = AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.SET,
            condition_operator=AutomationTrigger.ConditionOperator.AND,
            last_result=False,
        )
        AutomationAction.objects.create(
            automation=self.automation,
            trigger=trigger,
            order=1,
            action_type=AutomationAction.ActionType.SEQUENCE,
            sequence=self.sequence,
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            trigger=trigger,
            order=1,
            condition_type=AutomationCondition.ConditionType.DEVICE_STATE,
            config={
                "device_id": sensor.pk,
                "device_uid": sensor.device_uid,
                "key": "temperature",
                "operator": "lt",
                "value": 24,
            },
        )

        humidity_only = AutomationService.record_device_state(sensor, {"humidity": 51})
        temperature_change = AutomationService.record_device_state(sensor, {"temperature": 22})

        self.assertEqual(humidity_only, [])
        self.assertEqual(len(temperature_change), 1)

    def test_long_nested_mqtt_state_key_is_stored(self):
        long_key = ".".join(["bridge"] * 24)
        payload = current = {}
        for segment in long_key.split("."):
            current[segment] = {}
            current = current[segment]
        current["value"] = "ok"

        AutomationService.update_device_state("zigbee2mqtt/bridge/info", payload)

        self.assertTrue(
            DeviceState.objects.filter(
                topic="zigbee2mqtt/bridge/info",
                key=f"{long_key}.value",
                value="ok",
            ).exists()
        )


class SequenceWorkerTests(TestCase):
    def setUp(self):
        self.sequence = Sequence.objects.create(name="쿠키 갱신")
        self.media_server, _ = Device.objects.update_or_create(
            device_uid="pi5",
            defaults={
                "name": "Pi5 미디어 서버",
                "device_type": "media_server",
                "protocol": Device.Protocol.MQTT,
                "location": "거실",
            },
        )
        self.step = SequenceStep.objects.create(
            sequence=self.sequence,
            order=1,
            device=self.media_server,
            function="ytmusic.refresh_cookie",
        )

    @patch.object(
        DeviceService,
        "execute_step",
        return_value=(True, "갱신 완료"),
    )
    def test_worker_records_successful_step(self, execute):
        sequence_run = SequenceRun.objects.create(
            sequence=self.sequence,
            trigger=SequenceRun.Trigger.MANUAL,
        )

        result = SequenceExecutor.run_next_pending()

        self.assertEqual(result.pk, sequence_run.pk)
        sequence_run.refresh_from_db()
        self.assertEqual(sequence_run.status, SequenceRun.Status.SUCCESS)
        step_run = SequenceStepRun.objects.get(sequence_run=sequence_run)
        self.assertEqual(step_run.status, SequenceStepRun.Status.SUCCESS)
        execute.assert_called_once_with(self.step)

    @patch.object(
        DeviceService,
        "execute_step",
        return_value=(False, "응답 시간 초과"),
    )
    def test_worker_records_failure(self, execute):
        sequence_run = SequenceRun.objects.create(
            sequence=self.sequence,
            trigger=SequenceRun.Trigger.MANUAL,
        )

        SequenceExecutor.run_next_pending()

        sequence_run.refresh_from_db()
        self.assertEqual(sequence_run.status, SequenceRun.Status.FAILED)
        self.assertIn("시간 초과", sequence_run.message)
        self.assertEqual(
            sequence_run.step_runs.get().status,
            SequenceStepRun.Status.FAILED,
        )

    @patch(
        "iotcore.device.services.device_service.IRClient.send_ir_request",
        return_value=(False, "ESP32 응답 시간 초과"),
    )
    @patch(
        "iotcore.device.services.device_service.IRCodeRepository.get_ir_code",
        return_value="0xB27BE0",
    )
    @patch(
        "iotcore.device.services.device_service.ControllerRepository.get_controller",
        return_value=SimpleNamespace(ip_address="192.168.0.10"),
    )
    def test_ir_failure_is_returned_as_failure(self, controller, code, send):
        success, message = DeviceService.execute_ir(1, "power_off")

        self.assertFalse(success)
        self.assertEqual(message, "ESP32 응답 시간 초과")


class AutomationExecutorTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="test-light",
            name="테스트 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        self.automation = Automation.objects.create(name="개별 동작 예약 실행")
        self.action = AutomationAction.objects.create(
            automation=self.automation,
            order=1,
            action_type=AutomationAction.ActionType.DEVICE,
            device=self.device,
            function="power_on",
        )

    @patch.object(DeviceService, "execute_step", return_value=(True, "켜짐"))
    def test_device_action_run_is_executed(self, execute):
        automation_run = AutomationRun.objects.create(automation=self.automation)
        result = AutomationExecutor.run_next_pending()
        self.assertEqual(result.pk, automation_run.pk)
        automation_run.refresh_from_db()
        self.assertEqual(automation_run.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(automation_run.action_runs.count(), 1)
        execute.assert_called_once()

    @patch.object(DeviceService, "execute_step", return_value=(True, "완료"))
    def test_executor_runs_only_actions_matched_at_trigger_time(self, execute):
        second_device = Device.objects.create(
            device_uid="test-aircon",
            name="테스트 에어컨",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="방",
        )
        second_action = AutomationAction.objects.create(
            automation=self.automation,
            order=2,
            action_type=AutomationAction.ActionType.DEVICE,
            device=second_device,
            function="power_off",
        )
        automation_run = AutomationRun.objects.create(
            automation=self.automation,
            trigger_payload={MATCHED_ACTION_IDS_KEY: [second_action.pk]},
        )

        AutomationExecutor.run_next_pending()

        automation_run.refresh_from_db()
        self.assertEqual(automation_run.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(automation_run.action_runs.count(), 1)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0].device, second_device)


class AutomationViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="automation-test",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.sequence = Sequence.objects.create(name="화면 테스트")

    def test_create_page_starts_without_trigger_or_action_cards(self):
        response = self.client.get(reverse("iotcore:schedule_create"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["trigger_formset"].total_form_count(), 0)
        self.assertEqual(response.context["condition_formset"].total_form_count(), 0)
        self.assertEqual(response.context["action_formset"].total_form_count(), 0)
        self.assertContains(response, "+ 트리거 세트 추가")
        self.assertNotContains(response, "+ 실행 규칙 추가")

    def test_create_saves_trigger_set_condition_and_action(self):
        device = Device.objects.create(
            device_uid="automation-light",
            name="예약 실행 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="거실",
        )
        response = self.client.post(
            reverse("iotcore:schedule_create"),
            {
                "name": "퇴근 예약 실행",
                "enabled": "on",
                "cooldown_seconds": "30",
                "triggers-TOTAL_FORMS": "1",
                "triggers-INITIAL_FORMS": "0",
                "triggers-MIN_NUM_FORMS": "0",
                "triggers-MAX_NUM_FORMS": "1000",
                "triggers-0-enabled": "on",
                "triggers-0-condition_operator": AutomationTrigger.ConditionOperator.AND,
                "conditions-TOTAL_FORMS": "1",
                "conditions-INITIAL_FORMS": "0",
                "conditions-MIN_NUM_FORMS": "0",
                "conditions-MAX_NUM_FORMS": "1000",
                "conditions-0-trigger_index": "0",
                "conditions-0-condition_type": AutomationCondition.ConditionType.MQTT_EVENT,
                "conditions-0-mqtt_topic": "zigbee2mqtt/front_door",
                "conditions-0-mqtt_field": "contact",
                "conditions-0-mqtt_operator": "changed_to",
                "conditions-0-mqtt_value": "false",
                "actions-TOTAL_FORMS": "1",
                "actions-INITIAL_FORMS": "0",
                "actions-MIN_NUM_FORMS": "0",
                "actions-MAX_NUM_FORMS": "1000",
                "actions-0-action_type": AutomationAction.ActionType.DEVICE,
                "actions-0-device": str(device.pk),
                "actions-0-function": "power_on",
                "actions-0-delay": "0",
            },
        )

        self.assertRedirects(response, reverse("iotcore:schedule_list"))
        automation = Automation.objects.get(name="퇴근 예약 실행")
        trigger = automation.triggers.get()
        self.assertEqual(trigger.trigger_type, AutomationTrigger.TriggerType.SET)
        self.assertEqual(trigger.condition_operator, AutomationTrigger.ConditionOperator.AND)
        self.assertEqual(trigger.conditions.count(), 1)
        condition = trigger.conditions.get()
        self.assertEqual(condition.condition_type, AutomationCondition.ConditionType.MQTT_EVENT)
        self.assertEqual(condition.config["topic"], "zigbee2mqtt/front_door")
        action = automation.actions.get()
        self.assertEqual(action.trigger, trigger)
        self.assertEqual(action.device, device)
        self.assertEqual(action.function, "power_on")

    def test_create_saves_conditions_under_each_trigger_set(self):
        light = Device.objects.create(
            device_uid="rule-form-light",
            name="규칙 폼 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="방",
        )
        aircon = Device.objects.create(
            device_uid="rule-form-aircon",
            name="규칙 폼 에어컨",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="방",
        )
        response = self.client.post(
            reverse("iotcore:schedule_create"),
            {
                "name": "트리거 세트 조건 테스트",
                "enabled": "on",
                "cooldown_seconds": "0",
                "triggers-TOTAL_FORMS": "2",
                "triggers-INITIAL_FORMS": "0",
                "triggers-MIN_NUM_FORMS": "0",
                "triggers-MAX_NUM_FORMS": "1000",
                "triggers-0-enabled": "on",
                "triggers-0-condition_operator": AutomationTrigger.ConditionOperator.AND,
                "triggers-1-enabled": "on",
                "triggers-1-condition_operator": AutomationTrigger.ConditionOperator.OR,
                "conditions-TOTAL_FORMS": "2",
                "conditions-INITIAL_FORMS": "0",
                "conditions-MIN_NUM_FORMS": "0",
                "conditions-MAX_NUM_FORMS": "1000",
                "conditions-0-trigger_index": "0",
                "conditions-0-condition_type": AutomationCondition.ConditionType.DEVICE_STATE,
                "conditions-0-state_device": str(light.pk),
                "conditions-0-state_key": "power",
                "conditions-0-state_operator": "eq",
                "conditions-0-state_value": "true",
                "conditions-1-trigger_index": "1",
                "conditions-1-condition_type": AutomationCondition.ConditionType.DEVICE_STATE,
                "conditions-1-state_device": str(aircon.pk),
                "conditions-1-state_key": "power",
                "conditions-1-state_operator": "eq",
                "conditions-1-state_value": "true",
                "actions-TOTAL_FORMS": "2",
                "actions-INITIAL_FORMS": "0",
                "actions-MIN_NUM_FORMS": "0",
                "actions-MAX_NUM_FORMS": "1000",
                "actions-0-action_type": AutomationAction.ActionType.DEVICE,
                "actions-0-device": str(light.pk),
                "actions-0-function": "power_off",
                "actions-0-delay": "0",
                "actions-1-action_type": AutomationAction.ActionType.DEVICE,
                "actions-1-device": str(aircon.pk),
                "actions-1-function": "power_off",
                "actions-1-delay": "0",
            },
        )

        self.assertRedirects(response, reverse("iotcore:schedule_list"))
        automation = Automation.objects.get(name="트리거 세트 조건 테스트")
        actions = list(automation.actions.order_by("order"))
        self.assertEqual(len(actions), 2)
        self.assertEqual(automation.triggers.count(), 2)
        self.assertEqual(actions[0].trigger.condition_operator, AutomationTrigger.ConditionOperator.AND)
        self.assertEqual(actions[1].trigger.condition_operator, AutomationTrigger.ConditionOperator.OR)
        self.assertEqual(actions[0].trigger.conditions.count(), 1)
        self.assertEqual(actions[1].trigger.conditions.count(), 1)
        self.assertEqual(actions[0].conditions.count(), 0)
        self.assertEqual(actions[1].conditions.count(), 0)
        self.assertEqual(
            actions[0].trigger.conditions.get().config["device_id"],
            light.pk,
        )
        self.assertEqual(
            actions[1].trigger.conditions.get().config["device_id"],
            aircon.pk,
        )

    def test_new_trigger_set_defaults_to_enabled_when_checkbox_is_omitted(self):
        device = Device.objects.create(
            device_uid="default-trigger-light",
            name="기본 활성화 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="거실",
        )
        run_at = timezone.localtime(timezone.now() + timedelta(minutes=10))
        response = self.client.post(
            reverse("iotcore:schedule_create"),
            {
                "name": "기본 활성화 테스트",
                "enabled": "on",
                "cooldown_seconds": "0",
                "triggers-TOTAL_FORMS": "1",
                "triggers-INITIAL_FORMS": "0",
                "triggers-MIN_NUM_FORMS": "0",
                "triggers-MAX_NUM_FORMS": "1000",
                "triggers-0-condition_operator": AutomationTrigger.ConditionOperator.AND,
                "conditions-TOTAL_FORMS": "1",
                "conditions-INITIAL_FORMS": "0",
                "conditions-MIN_NUM_FORMS": "0",
                "conditions-MAX_NUM_FORMS": "1000",
                "conditions-0-trigger_index": "0",
                "conditions-0-condition_type": AutomationCondition.ConditionType.SCHEDULE,
                "conditions-0-schedule_type": AutomationTrigger.ScheduleType.ONCE,
                "conditions-0-run_at": run_at.strftime("%Y-%m-%dT%H:%M"),
                "actions-TOTAL_FORMS": "1",
                "actions-INITIAL_FORMS": "0",
                "actions-MIN_NUM_FORMS": "0",
                "actions-MAX_NUM_FORMS": "1000",
                "actions-0-action_type": AutomationAction.ActionType.DEVICE,
                "actions-0-device": str(device.pk),
                "actions-0-function": "power_on",
                "actions-0-delay": "0",
            },
        )

        self.assertRedirects(response, reverse("iotcore:schedule_list"))
        trigger = AutomationTrigger.objects.get(
            automation__name="기본 활성화 테스트"
        )
        self.assertTrue(trigger.enabled)
        self.assertEqual(trigger.trigger_type, AutomationTrigger.TriggerType.SET)
        self.assertIsNotNone(trigger.next_run_at)
        self.assertEqual(trigger.conditions.count(), 1)

    def test_list_card_opens_edit_without_edit_button(self):
        automation = Automation.objects.create(name="카드 예약 실행")

        response = self.client.get(reverse("iotcore:schedule_list"))
        edit_url = reverse(
            "iotcore:schedule_update",
            kwargs={"schedule_id": automation.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "예약 실행 목록")
        self.assertContains(response, f'data-edit-url="{edit_url}"')
        self.assertNotContains(response, ">편집<")

    def test_toggle_recalculates_time_trigger(self):
        automation = Automation.objects.create(
            name="아침 예약 실행",
            enabled=False,
        )
        trigger = AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=AutomationTrigger.TriggerType.TIME,
            config={
                "schedule_type": AutomationTrigger.ScheduleType.DAILY,
                "time": "08:30",
            },
            enabled=True,
        )

        response = self.client.post(
            reverse(
                "iotcore:schedule_toggle",
                kwargs={"schedule_id": automation.id},
            )
        )

        self.assertRedirects(response, reverse("iotcore:schedule_list"))
        trigger.refresh_from_db()
        self.assertIsNotNone(trigger.next_run_at)

    def test_manual_sequence_run_is_enqueued(self):
        response = self.client.post(
            reverse(
                "iotcore:sequence_run",
                kwargs={"sequence_id": self.sequence.id},
            )
        )

        self.assertRedirects(response, reverse("iotcore:sequence_list"))
        run = SequenceRun.objects.get()
        self.assertEqual(run.trigger, SequenceRun.Trigger.MANUAL)
        self.assertEqual(run.status, SequenceRun.Status.PENDING)

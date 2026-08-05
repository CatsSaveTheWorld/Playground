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
    AutomationTriggerForm,
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
from .scheduler.calculator import calculate_next_run
from .scheduler.executor import AutomationExecutor
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


class AutomationTriggerFormTests(TestCase):
    def test_mqtt_value_is_parsed_as_json_type(self):
        form = AutomationTriggerForm(data={
            "trigger_type": AutomationTrigger.TriggerType.MQTT_EVENT,
            "enabled": "on",
            "event_topic": "zigbee2mqtt/front_door",
            "event_field": "contact",
            "event_operator": "eq",
            "event_value": "true",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIs(form.cleaned_data["config"]["value"], True)

    def test_hidden_time_fields_do_not_leak_into_mqtt_config(self):
        form = AutomationTriggerForm(data={
            "trigger_type": AutomationTrigger.TriggerType.MQTT_EVENT,
            "enabled": "on",
            "schedule_type": AutomationTrigger.ScheduleType.DAILY,
            "time_of_day": "08:30",
            "event_topic": "zigbee2mqtt/front_door",
            "event_field": "contact",
            "event_operator": "eq",
            "event_value": "false",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            set(form.cleaned_data["config"]),
            {"topic", "field", "operator", "value"},
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


class AutomationServiceTests(TestCase):
    def setUp(self):
        self.sequence = Sequence.objects.create(name="예약 시퀀스")
        self.automation = Automation.objects.create(name="자동화")

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
        self.automation = Automation.objects.create(name="개별 동작 자동화")
        AutomationAction.objects.create(
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
        self.assertContains(response, "+ 실행 트리거 추가")
        self.assertContains(response, "+ 실행 동작 추가")

    def test_create_saves_dynamically_added_trigger_and_action(self):
        device = Device.objects.create(
            device_uid="automation-light",
            name="자동화 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="거실",
        )
        response = self.client.post(
            reverse("iotcore:schedule_create"),
            {
                "name": "퇴근 자동화",
                "enabled": "on",
                "cooldown_seconds": "30",
                "triggers-TOTAL_FORMS": "1",
                "triggers-INITIAL_FORMS": "0",
                "triggers-MIN_NUM_FORMS": "0",
                "triggers-MAX_NUM_FORMS": "1000",
                "triggers-0-trigger_type": AutomationTrigger.TriggerType.MQTT_EVENT,
                "triggers-0-enabled": "on",
                "triggers-0-event_topic": "zigbee2mqtt/front_door",
                "triggers-0-event_field": "contact",
                "triggers-0-event_operator": "changed_to",
                "triggers-0-event_value": "false",
                "conditions-TOTAL_FORMS": "0",
                "conditions-INITIAL_FORMS": "0",
                "conditions-MIN_NUM_FORMS": "0",
                "conditions-MAX_NUM_FORMS": "1000",
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
        automation = Automation.objects.get(name="퇴근 자동화")
        self.assertEqual(automation.triggers.count(), 1)
        self.assertEqual(automation.conditions.count(), 0)
        action = automation.actions.get()
        self.assertEqual(action.device, device)
        self.assertEqual(action.function, "power_on")

    def test_list_card_opens_edit_without_edit_button(self):
        automation = Automation.objects.create(name="카드 자동화")

        response = self.client.get(reverse("iotcore:schedule_list"))
        edit_url = reverse(
            "iotcore:schedule_update",
            kwargs={"schedule_id": automation.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-edit-url="{edit_url}"')
        self.assertNotContains(response, ">편집<")

    def test_toggle_recalculates_time_trigger(self):
        automation = Automation.objects.create(
            name="아침 자동화",
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

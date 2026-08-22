from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import AutomationConditionForm
from .models import (
    Automation,
    AutomationAction,
    AutomationCondition,
    AutomationRun,
    AutomationTrigger,
    Device,
)
from .scheduler.calculator import describe_condition
from .scheduler.service import AutomationService


KST = ZoneInfo("Asia/Seoul")


class WeatherConditionFormTests(TestCase):
    def test_weather_condition_builds_numeric_config(self):
        form = AutomationConditionForm(
            data={
                "condition_type": AutomationCondition.ConditionType.WEATHER,
                "weather_metric": "temperature",
                "weather_operator": "lt",
                "weather_value": "24.5",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["config"],
            {"metric": "temperature", "operator": "lt", "value": 24.5},
        )

    def test_weather_percent_metric_accepts_zero_and_rejects_over_100(self):
        zero_form = AutomationConditionForm(
            data={
                "condition_type": AutomationCondition.ConditionType.WEATHER,
                "weather_metric": "precipitation_probability",
                "weather_operator": "lte",
                "weather_value": "0",
            }
        )
        invalid_form = AutomationConditionForm(
            data={
                "condition_type": AutomationCondition.ConditionType.WEATHER,
                "weather_metric": "humidity",
                "weather_operator": "gte",
                "weather_value": "101",
            }
        )

        self.assertTrue(zero_form.is_valid(), zero_form.errors)
        self.assertEqual(zero_form.cleaned_data["config"]["value"], 0.0)
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("weather_value", invalid_form.errors)

    def test_weather_condition_restores_existing_values_and_summary(self):
        automation = Automation.objects.create(name="날씨 설정 복원")
        condition = AutomationCondition.objects.create(
            automation=automation,
            condition_type=AutomationCondition.ConditionType.WEATHER,
            config={"metric": "humidity", "operator": "gte", "value": 80.0},
        )

        form = AutomationConditionForm(instance=condition)

        self.assertEqual(form["weather_metric"].value(), "humidity")
        self.assertEqual(form["weather_operator"].value(), "gte")
        self.assertEqual(form["weather_value"].value(), 80.0)
        self.assertEqual(describe_condition(condition), "현재 날씨 · 현재 습도 ≥ 80%")


class WeatherAutomationServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        AutomationService._last_weather_evaluation_token = None
        self.automation = Automation.objects.create(name="저온 에어컨 끄기")
        self.trigger = AutomationTrigger.objects.create(
            automation=self.automation,
            trigger_type=AutomationTrigger.TriggerType.SET,
            condition_operator=AutomationTrigger.ConditionOperator.AND,
            last_result=False,
        )
        AutomationCondition.objects.create(
            automation=self.automation,
            trigger=self.trigger,
            condition_type=AutomationCondition.ConditionType.WEATHER,
            config={"metric": "temperature", "operator": "lt", "value": 24.0},
            order=1,
        )
        aircon = Device.objects.create(
            device_uid="weather-aircon",
            name="날씨 자동화 에어컨",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="방",
        )
        AutomationAction.objects.create(
            automation=self.automation,
            trigger=self.trigger,
            action_type=AutomationAction.ActionType.DEVICE,
            device=aircon,
            function="power_off",
            order=1,
        )

    @staticmethod
    def weather(temperature, fetched_at, *, stale=False):
        return {
            "location": "송탄",
            "temperature": temperature,
            "humidity": 60.0,
            "precipitation_probability": 10,
            "condition": "맑음",
            "high": 29.0,
            "low": 20.0,
            "updated_at": fetched_at - timedelta(minutes=5),
            "fetched_at": fetched_at,
            "stale": stale,
            "source": "기상청",
        }

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot")
    def test_weather_false_to_true_enqueues_once_and_rearms(self, snapshot):
        first_at = datetime(2026, 8, 22, 10, 0, tzinfo=KST)
        snapshot.return_value = self.weather(25.0, first_at)
        self.assertEqual(AutomationService.process_weather_conditions(now=first_at), [])

        second_at = first_at + timedelta(minutes=30)
        snapshot.return_value = self.weather(23.5, second_at)
        first_runs = AutomationService.process_weather_conditions(now=second_at)
        self.assertEqual(len(first_runs), 1)
        self.assertEqual(
            first_runs[0].trigger_payload["weather"]["temperature"],
            23.5,
        )
        self.assertIsInstance(
            first_runs[0].trigger_payload["weather"]["fetched_at"],
            str,
        )
        self.assertEqual(
            AutomationService.process_weather_conditions(now=second_at),
            [],
        )

        third_at = second_at + timedelta(minutes=30)
        snapshot.return_value = self.weather(25.0, third_at)
        self.assertEqual(AutomationService.process_weather_conditions(now=third_at), [])

        fourth_at = third_at + timedelta(minutes=30)
        snapshot.return_value = self.weather(23.0, fourth_at)
        second_runs = AutomationService.process_weather_conditions(now=fourth_at)
        self.assertEqual(len(second_runs), 1)
        self.assertEqual(AutomationRun.objects.count(), 2)

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot")
    def test_stale_weather_never_enqueues(self, snapshot):
        now = timezone.now()
        snapshot.return_value = self.weather(20.0, now, stale=True)

        self.assertEqual(AutomationService.process_weather_conditions(now=now), [])
        self.assertEqual(AutomationRun.objects.count(), 0)

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot")
    def test_scheduler_does_not_fetch_weather_without_weather_triggers(self, snapshot):
        self.automation.delete()

        self.assertEqual(AutomationService.process_weather_conditions(), [])
        snapshot.assert_not_called()

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot")
    def test_weather_provider_exception_does_not_stop_scheduler(self, snapshot):
        snapshot.side_effect = RuntimeError("provider unavailable")

        self.assertEqual(AutomationService.process_weather_conditions(), [])
        self.trigger.refresh_from_db()
        self.assertFalse(self.trigger.last_result)
        self.assertEqual(AutomationRun.objects.count(), 0)

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot")
    def test_resting_state_refresh_does_not_call_weather_provider(self, snapshot):
        self.assertFalse(AutomationService.refresh_trigger_result(self.trigger))

        snapshot.assert_not_called()

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot")
    def test_exact_schedule_and_weather_share_one_snapshot(self, snapshot):
        now = datetime(2026, 8, 22, 11, 0, tzinfo=KST)
        schedule = AutomationCondition.objects.create(
            automation=self.automation,
            trigger=self.trigger,
            condition_type=AutomationCondition.ConditionType.SCHEDULE,
            config={
                "schedule_type": AutomationTrigger.ScheduleType.ONCE,
                "run_at": now.isoformat(),
            },
            order=2,
        )
        self.trigger.next_run_at = now
        self.trigger.save(update_fields=["next_run_at"])
        snapshot.return_value = self.weather(20.0, now)

        runs = AutomationService.enqueue_due(now=now)

        self.assertEqual(len(runs), 1)
        self.assertEqual(
            runs[0].trigger_payload["source_condition_id"],
            schedule.pk,
        )
        self.assertEqual(runs[0].trigger_payload["weather"]["temperature"], 20.0)
        snapshot.assert_called_once_with()

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot")
    def test_missing_metric_does_not_make_not_equal_condition_true(self, snapshot):
        condition = self.trigger.conditions.get()
        condition.config = {
            "metric": "precipitation_probability",
            "operator": "ne",
            "value": 50.0,
        }
        condition.save(update_fields=["config"])
        self.trigger.last_result = True
        self.trigger.save(update_fields=["last_result"])
        now = timezone.now()
        weather = self.weather(20.0, now)
        weather["precipitation_probability"] = None
        snapshot.return_value = weather

        self.assertEqual(AutomationService.process_weather_conditions(now=now), [])
        self.trigger.refresh_from_db()
        self.assertTrue(self.trigger.last_result)
        self.assertEqual(AutomationRun.objects.count(), 0)


class WeatherAutomationViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="weather-automation-test",
            password="test-password",
        )
        self.client.force_login(user)
        self.aircon = Device.objects.create(
            device_uid="weather-form-aircon",
            name="날씨 조건 에어컨",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="방",
        )

    def test_create_page_contains_weather_fields_for_dynamic_conditions(self):
        response = self.client.get(reverse("iotcore:schedule_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "현재 날씨")
        self.assertContains(response, "id_conditions-__prefix__-weather_metric")

    @patch("iotcore.scheduler.service.KmaWeatherService.snapshot", return_value=None)
    def test_weather_only_trigger_saves_aircon_power_off_action(self, _snapshot):
        response = self.client.post(
            reverse("iotcore:schedule_create"),
            {
                "name": "기온 낮으면 에어컨 끄기",
                "enabled": "on",
                "cooldown_seconds": "0",
                "triggers-TOTAL_FORMS": "1",
                "triggers-INITIAL_FORMS": "0",
                "triggers-MIN_NUM_FORMS": "0",
                "triggers-MAX_NUM_FORMS": "1000",
                "triggers-0-set_key": "weather-set-1",
                "triggers-0-enabled": "on",
                "triggers-0-condition_operator": AutomationTrigger.ConditionOperator.AND,
                "conditions-TOTAL_FORMS": "1",
                "conditions-INITIAL_FORMS": "0",
                "conditions-MIN_NUM_FORMS": "0",
                "conditions-MAX_NUM_FORMS": "1000",
                "conditions-0-trigger_key": "weather-set-1",
                "conditions-0-condition_type": AutomationCondition.ConditionType.WEATHER,
                "conditions-0-weather_metric": "temperature",
                "conditions-0-weather_operator": "lt",
                "conditions-0-weather_value": "24",
                "actions-TOTAL_FORMS": "1",
                "actions-INITIAL_FORMS": "0",
                "actions-MIN_NUM_FORMS": "0",
                "actions-MAX_NUM_FORMS": "1000",
                "actions-0-trigger_key": "weather-set-1",
                "actions-0-action_type": AutomationAction.ActionType.DEVICE,
                "actions-0-device": str(self.aircon.pk),
                "actions-0-function": "power_off",
                "actions-0-delay": "0",
            },
        )

        self.assertRedirects(response, reverse("iotcore:schedule_list"))
        automation = Automation.objects.get(name="기온 낮으면 에어컨 끄기")
        condition = automation.conditions.get()
        action = automation.actions.get()
        self.assertEqual(condition.condition_type, AutomationCondition.ConditionType.WEATHER)
        self.assertEqual(
            condition.config,
            {"metric": "temperature", "operator": "lt", "value": 24.0},
        )
        self.assertEqual(action.device, self.aircon)
        self.assertEqual(action.function, "power_off")

from datetime import datetime, time, timedelta
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse

from .device.services.device_service import DeviceService
from .device.services.sequence_executor import SequenceExecutor
from .models import (
    Device,
    Schedule,
    Sequence,
    SequenceRun,
    SequenceStep,
    SequenceStepRun,
)
from .infrastructure.remote_tasks.client import RemoteTaskClient
from .scheduler.calculator import calculate_next_run
from .scheduler.service import SchedulerService


class ScheduleCalculatorTests(TestCase):
    def setUp(self):
        self.sequence = Sequence.objects.create(name="테스트 시퀀스")

    def test_daily_schedule_uses_next_local_time(self):
        schedule = Schedule(
            sequence=self.sequence,
            name="매일",
            trigger_type=Schedule.TriggerType.DAILY,
            trigger_config={"time": "08:30"},
        )
        after = timezone.make_aware(datetime(2026, 8, 1, 9, 0))

        next_run = timezone.localtime(calculate_next_run(schedule, after=after))

        self.assertEqual(next_run.date().isoformat(), "2026-08-02")
        self.assertEqual(next_run.time().replace(tzinfo=None), time(8, 30))

    def test_weekly_schedule_selects_enabled_weekday(self):
        schedule = Schedule(
            sequence=self.sequence,
            name="월요일",
            trigger_type=Schedule.TriggerType.WEEKLY,
            trigger_config={"time": "07:00", "weekdays": [0]},
        )
        after = timezone.make_aware(datetime(2026, 8, 1, 9, 0))

        next_run = timezone.localtime(calculate_next_run(schedule, after=after))

        self.assertEqual(next_run.date().isoformat(), "2026-08-03")
        self.assertEqual(next_run.weekday(), 0)


class SchedulerServiceTests(TestCase):
    def setUp(self):
        self.sequence = Sequence.objects.create(name="예약 시퀀스")

    def test_due_once_schedule_is_enqueued_and_disabled(self):
        now = timezone.now()
        scheduled_for = now - timedelta(seconds=1)
        schedule = Schedule.objects.create(
            sequence=self.sequence,
            name="한 번 실행",
            trigger_type=Schedule.TriggerType.ONCE,
            trigger_config={"run_at": scheduled_for.isoformat()},
            next_run_at=scheduled_for,
        )

        runs = SchedulerService.enqueue_due(now=now)

        self.assertEqual(len(runs), 1)
        run = SequenceRun.objects.get()
        self.assertEqual(run.trigger, SequenceRun.Trigger.SCHEDULE)
        self.assertEqual(run.status, SequenceRun.Status.PENDING)
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.assertIsNone(schedule.next_run_at)

    def test_interval_schedule_advances_and_does_not_duplicate(self):
        now = timezone.now()
        scheduled_for = now - timedelta(minutes=1)
        schedule = Schedule.objects.create(
            sequence=self.sequence,
            name="간격 실행",
            trigger_type=Schedule.TriggerType.INTERVAL,
            trigger_config={"every": 5, "unit": "minutes"},
            next_run_at=scheduled_for,
        )

        first = SchedulerService.enqueue_due(now=now)
        second = SchedulerService.enqueue_due(now=now)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(SequenceRun.objects.count(), 1)
        schedule.refresh_from_db()
        self.assertGreater(schedule.next_run_at, now)


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
        return_value=(True, "쿠키와 Music Assistant 설정을 갱신했습니다."),
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
        return_value=(False, "Pi 에이전트 응답 시간 초과"),
    )
    def test_worker_stops_and_records_failure(self, execute):
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

    @patch.object(
        RemoteTaskClient,
        "execute",
        return_value=(True, "갱신 완료"),
    )
    def test_media_server_uses_device_uid_as_agent_id(self, execute):
        result = DeviceService.execute_media_server(self.step)

        self.assertEqual(result, (True, "갱신 완료"))
        execute.assert_called_once_with(
            action="ytmusic.refresh_cookie",
            parameters={},
            agent_id="pi5",
        )


class SchedulerViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="scheduler-test",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.sequence = Sequence.objects.create(name="화면 테스트")
        self.media_server, _ = Device.objects.update_or_create(
            device_uid="pi5",
            defaults={
                "name": "Pi5 미디어 서버",
                "device_type": "media_server",
                "protocol": Device.Protocol.MQTT,
                "location": "거실",
            },
        )

    def test_schedule_list_renders_schedule_summary(self):
        Schedule.objects.create(
            sequence=self.sequence,
            name="아침 실행",
            trigger_type=Schedule.TriggerType.DAILY,
            trigger_config={"time": "08:30"},
            next_run_at=timezone.now() + timedelta(days=1),
        )

        response = self.client.get(reverse("iotcore:schedule_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "예약 실행")
        self.assertContains(response, "아침 실행")
        self.assertContains(response, "매일 08:30")

    def test_sequence_edit_renders_media_server_step(self):
        SequenceStep.objects.create(
            sequence=self.sequence,
            order=1,
            device=self.media_server,
            function="ytmusic.refresh_cookie",
        )

        response = self.client.get(
            reverse(
                "iotcore:sequence_edit",
                kwargs={"sequence_id": self.sequence.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "YouTube Music 쿠키 갱신")
        self.assertContains(response, "Pi5 미디어 서버")

    def test_manual_run_is_enqueued(self):
        response = self.client.post(
            reverse(
                "iotcore:sequence_run",
                kwargs={"sequence_id": self.sequence.id},
            )
        )

        self.assertRedirects(response, reverse("iotcore:sequence_list"))
        sequence_run = SequenceRun.objects.get()
        self.assertEqual(sequence_run.trigger, SequenceRun.Trigger.MANUAL)
        self.assertEqual(sequence_run.status, SequenceRun.Status.PENDING)


class RemoteTaskClientTests(TestCase):
    class FakeMQTTClient:
        def __init__(self, *args, **kwargs):
            self.connected = False
            self.on_connect = None
            self.on_message = None
            self.on_subscribe = None

        def connect(self, host, port, keepalive):
            self.connected = True
            self.on_connect(self, None, None, 0)

        def subscribe(self, topic, qos):
            self.on_subscribe(self, None, 1, [0])

        def is_connected(self):
            return self.connected

        def loop_start(self):
            pass

        def loop_stop(self):
            pass

        def disconnect(self):
            self.connected = False

        def publish(self, topic, payload, qos):
            request = json.loads(payload)
            message = SimpleNamespace(
                payload=json.dumps(
                    {
                        "success": True,
                        "message": "갱신 완료",
                        "request_id": request["request_id"],
                    }
                ).encode("utf-8")
            )
            self.on_message(self, None, message)
            return SimpleNamespace(rc=0)

    @override_settings(
        MQTT_HOST="127.0.0.1",
        MQTT_PORT=1883,
        IOTCORE_REMOTE_TASK_TIMEOUT=1,
    )
    @patch(
        "iotcore.infrastructure.remote_tasks.client.mqtt.Client",
        FakeMQTTClient,
    )
    def test_correlated_mqtt_result_is_returned(self):
        success, message = RemoteTaskClient.execute(
            "ytmusic.refresh_cookie",
        )

        self.assertTrue(success)
        self.assertEqual(message, "갱신 완료")

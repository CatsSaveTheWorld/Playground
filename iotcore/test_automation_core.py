from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Action, Automation, AutomationRun, Device, Step, Trigger
from .scheduler.executor import AutomationExecutor
from .scheduler.service import AutomationService


class AutomationCoreTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_type="projector",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="test-projector",
            name="테스트 프로젝터",
            location="test",
        )

    def _automation_with_action(self, automation_type=Automation.Type.IMMEDIATE):
        automation = Automation.objects.create(
            name="테스트 자동화",
            automation_type=automation_type,
        )
        step = Step.objects.create(automation=automation, order=1)
        action = Action.objects.create(
            step=step,
            order=1,
            action_type=Action.Type.DEVICE,
            device=self.device,
            function="power_on",
        )
        return automation, step, action

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_immediate_step_without_trigger_runs_unconditionally(self, execute_step):
        automation, _, _ = self._automation_with_action()
        success, _ = AutomationExecutor.execute(automation)
        self.assertTrue(success)
        execute_step.assert_called_once()
        run = AutomationRun.objects.latest("id")
        self.assertEqual(run.source, AutomationRun.Source.MANUAL)
        self.assertEqual(run.status, AutomationRun.Status.SUCCESS)

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_manual_run_respects_trigger(self, execute_step):
        automation, step, _ = self._automation_with_action()
        Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={
                "device_id": self.device.pk,
                "device_uid": self.device.device_uid,
                "key": "power",
                "operator": "eq",
                "value": True,
            },
        )
        success, message = AutomationExecutor.execute(automation)
        self.assertTrue(success)
        self.assertIn("없습니다", message)
        execute_step.assert_not_called()
    def test_scheduled_step_can_have_zero_triggers_but_is_not_auto_woken(self):
        automation, step, _ = self._automation_with_action(Automation.Type.SCHEDULED)
        AutomationService.recalculate_step(step)
        step.refresh_from_db()
        self.assertIsNone(step.next_run_at)
        self.assertEqual(AutomationService.enqueue_due(), [])

    def test_once_schedule_creates_next_run(self):
        automation, step, _ = self._automation_with_action(Automation.Type.SCHEDULED)
        run_at = timezone.now() + timezone.timedelta(minutes=10)
        Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.SCHEDULE,
            config={"schedule_type": Trigger.ScheduleType.ONCE, "run_at": run_at.isoformat()},
        )
        AutomationService.recalculate_step(step)
        step.refresh_from_db()
        self.assertIsNotNone(step.next_run_at)

    def test_nested_automation_action_uses_same_engine(self):
        child, _, _ = self._automation_with_action()
        parent = Automation.objects.create(name="부모", automation_type=Automation.Type.IMMEDIATE)
        step = Step.objects.create(automation=parent, order=1)
        Action.objects.create(
            step=step,
            order=1,
            action_type=Action.Type.AUTOMATION,
            target_automation=child,
        )
        with patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok")) as execute_step:
            success, _ = AutomationExecutor.execute(parent)
        self.assertTrue(success)
        self.assertEqual(execute_step.call_count, 1)
        self.assertTrue(AutomationRun.objects.filter(automation=child, source=AutomationRun.Source.AUTOMATION).exists())

    def test_ai_source_is_a_normal_execution_source(self):
        automation, _, _ = self._automation_with_action()
        run = AutomationExecutor.enqueue(automation, source=AutomationRun.Source.AI)
        self.assertEqual(run.source, AutomationRun.Source.AI)

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_device_delay_does_not_block_another_device(self, execute_step):
        aircon = Device.objects.create(
            device_type="aircon",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="test-aircon",
            name="테스트 에어컨",
            location="test",
        )
        automation = Automation.objects.create(
            name="기기별 큐 테스트",
            automation_type=Automation.Type.IMMEDIATE,
        )
        fan_step = Step.objects.create(automation=automation, order=1)
        Action.objects.create(
            step=fan_step,
            order=1,
            action_type=Action.Type.DEVICE,
            device=aircon,
            function="mode_fan",
            delay=3600,
            delay_position=Action.DelayPosition.AFTER,
        )
        off_step = Step.objects.create(automation=automation, order=2)
        Action.objects.create(
            step=off_step,
            order=1,
            action_type=Action.Type.DEVICE,
            device=aircon,
            function="power_off",
        )
        projector_step = Step.objects.create(automation=automation, order=3)
        Action.objects.create(
            step=projector_step,
            order=1,
            action_type=Action.Type.DEVICE,
            device=self.device,
            function="power_off",
        )

        success, message = AutomationExecutor.execute(automation)

        self.assertTrue(success)
        self.assertIn("지연", message)
        run = AutomationRun.objects.filter(root_run__isnull=True).latest("id")
        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.Status.RUNNING)
        self.assertEqual(execute_step.call_count, 2)
        self.assertEqual(
            list(
                run.queued_action_runs.order_by("id").values_list(
                    "status", flat=True
                )
            ),
            [
                AutomationRun.Status.SUCCESS,
                AutomationRun.Status.WAITING,
                AutomationRun.Status.SUCCESS,
            ],
        )

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_cancel_removes_delayed_work_without_device_command(self, execute_step):
        automation, _, action = self._automation_with_action()
        action.delay = 3600
        action.delay_position = Action.DelayPosition.BEFORE
        action.save(update_fields=["delay", "delay_position"])
        run = AutomationExecutor.enqueue(automation)

        AutomationExecutor.run_next_pending(root_run_id=run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.Status.RUNNING)
        self.assertEqual(
            run.queued_action_runs.get().status,
            AutomationRun.Status.WAITING,
        )

        cancelled, _ = AutomationExecutor.cancel_run(run)

        self.assertTrue(cancelled)
        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.Status.CANCELLED)
        self.assertEqual(
            run.queued_action_runs.get().status,
            AutomationRun.Status.CANCELLED,
        )
        execute_step.assert_not_called()


class AutomationCancellationViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="queue-operator",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.automation = Automation.objects.create(
            name="취소 화면 테스트",
            automation_type=Automation.Type.IMMEDIATE,
        )
        step = Step.objects.create(automation=self.automation, order=1)
        device = Device.objects.create(
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            device_uid="cancel-test-light",
            name="취소 테스트 전등",
            location="test",
        )
        Action.objects.create(
            step=step,
            order=1,
            device=device,
            function="power_off",
            delay=3600,
            delay_position=Action.DelayPosition.BEFORE,
        )

    def test_execution_panel_renders_cancel_control(self):
        run = AutomationExecutor.enqueue(self.automation)

        response = self.client.get(
            f"{reverse('iotcore:automation_list')}?type=immediate"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("iotcore:automation_run_cancel", args=[run.pk]),
        )
        self.assertContains(response, "대기 취소")

    def test_cancel_endpoint_cancels_waiting_tree(self):
        run = AutomationExecutor.enqueue(self.automation)
        AutomationExecutor.run_next_pending(root_run_id=run.pk)

        response = self.client.post(
            reverse("iotcore:automation_run_cancel", args=[run.pk]),
            {"next": reverse("iotcore:automation_list")},
        )

        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.Status.CANCELLED)
        self.assertEqual(
            run.queued_action_runs.get().status,
            AutomationRun.Status.CANCELLED,
        )

from unittest.mock import patch

from django.test import TestCase
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

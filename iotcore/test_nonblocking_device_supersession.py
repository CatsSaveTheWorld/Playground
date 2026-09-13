from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .models import Action, ActionRun, Automation, AutomationRun, Device, Step
from .scheduler.executor import AutomationExecutor
from .scheduler.supersession import DeviceControlSupersessionService


class NonBlockingAutomationDelayTests(TestCase):
    def setUp(self):
        self.aircon = Device.objects.create(
            device_uid="delay-aircon",
            name="Delay Aircon",
            device_type="aircon",
            protocol=Device.Protocol.IR,
            location="test",
        )
        self.light = Device.objects.create(
            device_uid="delay-light",
            name="Delay Light",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="test",
        )

    def _automation(self, name="delay-test"):
        return Automation.objects.create(
            name=name,
            automation_type=Automation.Type.IMMEDIATE,
        )

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_step_boundary_gets_default_half_second_delay(self, execute_step):
        automation = self._automation()
        step1 = Step.objects.create(automation=automation, order=1)
        step2 = Step.objects.create(automation=automation, order=2)
        Action.objects.create(
            step=step1,
            order=1,
            device=self.aircon,
            function="mode_cool",
        )
        Action.objects.create(
            step=step2,
            order=1,
            device=self.light,
            function="power_on",
        )

        run = AutomationExecutor.enqueue(automation)
        AutomationExecutor.run_next_pending()

        action_runs = list(run.action_runs.order_by("scheduled_for", "id"))
        self.assertEqual(len(action_runs), 2)
        self.assertEqual(action_runs[0].status, AutomationRun.Status.SUCCESS)
        self.assertEqual(action_runs[1].status, AutomationRun.Status.PENDING)
        gap = action_runs[1].scheduled_for - action_runs[0].finished_at
        self.assertGreaterEqual(gap.total_seconds(), 0.49)

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_long_after_delay_is_pending_in_db_not_worker_sleep(self, execute_step):
        automation = self._automation()
        step1 = Step.objects.create(automation=automation, order=1)
        step2 = Step.objects.create(automation=automation, order=2)
        Action.objects.create(
            step=step1,
            order=1,
            device=self.aircon,
            function="mode_fan",
            delay=3600,
            delay_position=Action.DelayPosition.AFTER,
        )
        Action.objects.create(
            step=step2,
            order=1,
            device=self.aircon,
            function="power_off",
        )

        run = AutomationExecutor.enqueue(automation)
        AutomationExecutor.run_next_pending()
        run.refresh_from_db()

        pending = run.action_runs.get(status=AutomationRun.Status.PENDING)
        self.assertEqual(run.status, AutomationRun.Status.RUNNING)
        self.assertGreater(
            (pending.scheduled_for - timezone.now()).total_seconds(),
            3500,
        )

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_new_step_control_supersedes_old_pending_same_device_only(self, execute_step):
        old = self._automation("old")
        old_step1 = Step.objects.create(automation=old, order=1)
        old_step2 = Step.objects.create(automation=old, order=2)
        Action.objects.create(
            step=old_step1,
            order=1,
            device=self.aircon,
            function="mode_fan",
            delay=3600,
            delay_position=Action.DelayPosition.AFTER,
        )
        Action.objects.create(
            step=old_step2,
            order=1,
            device=self.aircon,
            function="power_off",
        )

        old_run = AutomationExecutor.enqueue(old)
        AutomationExecutor.run_next_pending()
        old_pending = old_run.action_runs.get(status=AutomationRun.Status.PENDING)

        newer = self._automation("new")
        new_step = Step.objects.create(automation=newer, order=1)
        Action.objects.create(
            step=new_step,
            order=1,
            device=self.aircon,
            function="mode_cool",
        )
        AutomationExecutor.enqueue(newer)
        AutomationExecutor.run_next_pending()

        old_pending.refresh_from_db()
        old_run.refresh_from_db()
        self.assertEqual(old_pending.status, AutomationRun.Status.CANCELLED)
        self.assertEqual(old_run.status, AutomationRun.Status.SUCCESS)

    def test_manual_device_supersession_does_not_cancel_other_device(self):
        automation = self._automation()
        step = Step.objects.create(automation=automation, order=1)
        air_action = Action.objects.create(
            step=step,
            order=1,
            device=self.aircon,
            function="power_off",
        )
        light_action = Action.objects.create(
            step=step,
            order=2,
            device=self.light,
            function="power_off",
        )
        run = AutomationRun.objects.create(
            automation=automation,
            automation_name=automation.name,
            status=AutomationRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        air_run = ActionRun.objects.create(
            automation_run=run,
            action=air_action,
            step_order=1,
            order=1,
            status=AutomationRun.Status.PENDING,
            scheduled_for=timezone.now() + timezone.timedelta(hours=1),
        )
        light_run = ActionRun.objects.create(
            automation_run=run,
            action=light_action,
            step_order=1,
            order=2,
            status=AutomationRun.Status.PENDING,
            scheduled_for=timezone.now() + timezone.timedelta(hours=1),
        )

        DeviceControlSupersessionService.cancel_pending_for_device(self.aircon)
        air_run.refresh_from_db()
        light_run.refresh_from_db()
        run.refresh_from_db()

        self.assertEqual(air_run.status, AutomationRun.Status.CANCELLED)
        self.assertEqual(light_run.status, AutomationRun.Status.PENDING)
        self.assertEqual(run.status, AutomationRun.Status.RUNNING)

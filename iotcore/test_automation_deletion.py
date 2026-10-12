from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import Action, ActionRun, Automation, AutomationRun, Device, Step


class AutomationDeletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="delete-regression")
        self.client.force_login(self.user)
        self.target = Automation.objects.create(name="Retired temperature routine", enabled=False)
        self.url = reverse("iotcore:automation_delete", kwargs={"automation_id": self.target.pk})
        self.return_url = reverse("iotcore:automation_list") + "?type=scheduled"

    def make_reference(self):
        parent = Automation.objects.create(name="Bedtime routine", automation_type=Automation.Type.IMMEDIATE)
        step = Step.objects.create(automation=parent, order=1)
        action = Action.objects.create(step=step, order=1, action_type=Action.Type.AUTOMATION, target_automation=self.target)
        return parent, action

    def test_referenced_routine_reports_parent_and_preserves_graph(self):
        parent, action = self.make_reference()
        response = self.client.post(self.url, {"next": self.return_url})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.return_url)
        self.assertTrue(Automation.objects.filter(pk=self.target.pk).exists())
        self.assertTrue(Automation.objects.filter(pk=parent.pk).exists())
        action.refresh_from_db()
        self.assertEqual(action.target_automation_id, self.target.pk)
        texts = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any(parent.name in text and self.target.name in text for text in texts))

    def test_unreferenced_deletion_keeps_execution_history(self):
        device = Device.objects.create(device_type="projector", device_role=Device.Role.CONTROL, protocol=Device.Protocol.IR, device_uid="delete-test", name="Test device", location="test")
        step = Step.objects.create(automation=self.target, order=1)
        action = Action.objects.create(step=step, order=1, action_type=Action.Type.DEVICE, device=device, function="power_on")
        run = AutomationRun.objects.create(automation=self.target, automation_name=self.target.name, step=step, status=AutomationRun.Status.SUCCESS)
        history = ActionRun.objects.create(automation_run=run, root_run=run, action=action, order=1, action_type=Action.Type.DEVICE, device=device, function="power_on", status=AutomationRun.Status.SUCCESS)
        response = self.client.post(self.url, {"next": self.return_url})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Automation.objects.filter(pk=self.target.pk).exists())
        run.refresh_from_db()
        history.refresh_from_db()
        self.assertIsNone(run.automation_id)
        self.assertIsNone(run.step_id)
        self.assertEqual(run.automation_name, self.target.name)
        self.assertEqual(run.status, AutomationRun.Status.SUCCESS)
        self.assertIsNone(history.action_id)
        self.assertEqual(history.status, AutomationRun.Status.SUCCESS)

    def test_deletion_succeeds_after_reference_is_explicitly_removed(self):
        parent, action = self.make_reference()
        action.delete()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Automation.objects.filter(pk=self.target.pk).exists())
        self.assertTrue(Automation.objects.filter(pk=parent.pk).exists())

    def test_deletion_requires_post(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Automation.objects.filter(pk=self.target.pk).exists())

    def test_deletion_requires_login(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Automation.objects.filter(pk=self.target.pk).exists())

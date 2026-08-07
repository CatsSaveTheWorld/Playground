from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    Automation,
    AutomationAction,
    Sequence,
    SequenceRun,
)


class SequenceDeletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="sequence-delete-test",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.sequence = Sequence.objects.create(name="화면 테스트")

    def test_manual_run_stores_sequence_name_snapshot(self):
        response = self.client.post(
            reverse(
                "iotcore:sequence_run",
                kwargs={"sequence_id": self.sequence.id},
            )
        )

        self.assertRedirects(response, reverse("iotcore:sequence_list"))
        run = SequenceRun.objects.get()
        self.assertEqual(run.sequence_name, self.sequence.name)

    def test_delete_keeps_run_history_and_detaches_relation(self):
        completed_run = SequenceRun.objects.create(
            sequence=self.sequence,
            sequence_name=self.sequence.name,
            trigger=SequenceRun.Trigger.MANUAL,
            status=SequenceRun.Status.SUCCESS,
        )
        pending_run = SequenceRun.objects.create(
            sequence=self.sequence,
            sequence_name=self.sequence.name,
            trigger=SequenceRun.Trigger.MANUAL,
            status=SequenceRun.Status.PENDING,
        )

        response = self.client.post(
            reverse(
                "iotcore:sequence_delete",
                kwargs={"sequence_id": self.sequence.id},
            )
        )

        self.assertRedirects(response, reverse("iotcore:sequence_list"))
        self.assertFalse(Sequence.objects.filter(pk=self.sequence.pk).exists())

        completed_run.refresh_from_db()
        self.assertIsNone(completed_run.sequence)
        self.assertEqual(completed_run.sequence_name, "화면 테스트")
        self.assertEqual(completed_run.status, SequenceRun.Status.SUCCESS)

        pending_run.refresh_from_db()
        self.assertIsNone(pending_run.sequence)
        self.assertEqual(pending_run.sequence_name, "화면 테스트")
        self.assertEqual(pending_run.status, SequenceRun.Status.CANCELLED)

    def test_delete_referenced_by_automation_is_rejected_cleanly(self):
        automation = Automation.objects.create(name="시퀀스 참조 자동화")
        AutomationAction.objects.create(
            automation=automation,
            order=1,
            action_type=AutomationAction.ActionType.SEQUENCE,
            sequence=self.sequence,
        )

        response = self.client.post(
            reverse(
                "iotcore:sequence_delete",
                kwargs={"sequence_id": self.sequence.id},
            )
        )

        self.assertRedirects(response, reverse("iotcore:sequence_list"))
        self.assertTrue(Sequence.objects.filter(pk=self.sequence.pk).exists())

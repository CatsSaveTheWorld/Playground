from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    Automation,
    AutomationAction,
    AutomationGroup,
    AutomationTrigger,
    Device,
    Sequence,
    SequenceGroup,
    SequenceStep,
)


class LibraryOrganizationViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="library-test",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            device_uid="library-light",
            name="책상 전등",
            device_type="light",
            protocol=Device.Protocol.ZIGBEE,
            location="내 방",
        )

    def test_sequence_list_supports_group_search_and_favorite_scope(self):
        media = SequenceGroup.objects.create(name="미디어", order=10)
        favorite = Sequence.objects.create(
            name="영화 모드",
            description="프로젝터와 책상 전등을 준비",
            group=media,
            is_favorite=True,
        )
        SequenceStep.objects.create(
            sequence=favorite,
            order=1,
            device=self.device,
            function="power_on",
        )
        other = Sequence.objects.create(name="외출", description="외출 준비")

        response = self.client.get(
            reverse("iotcore:sequence_list"),
            {"q": "전등"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "영화 모드")
        self.assertNotContains(response, ">외출<")
        self.assertContains(response, "미디어")

        response = self.client.get(
            reverse("iotcore:sequence_list"),
            {"scope": "favorite"},
        )
        self.assertContains(response, favorite.name)
        self.assertNotContains(response, other.name)

    def test_sequence_favorite_toggle_preserves_requested_list_url(self):
        sequence = Sequence.objects.create(name="즐겨찾기 테스트")
        list_url = reverse("iotcore:sequence_list") + "?scope=ungrouped"

        response = self.client.post(
            reverse(
                "iotcore:sequence_favorite_toggle",
                kwargs={"sequence_id": sequence.pk},
            ),
            {"next": list_url},
        )

        self.assertRedirects(response, list_url)
        sequence.refresh_from_db()
        self.assertTrue(sequence.is_favorite)

    def test_deleting_sequence_group_moves_members_to_ungrouped(self):
        group = SequenceGroup.objects.create(name="삭제할 그룹", order=1)
        sequence = Sequence.objects.create(name="유지될 시퀀스", group=group)

        response = self.client.post(
            reverse("iotcore:sequence_group_manage"),
            {"action": "delete", "group_id": group.pk},
        )

        self.assertRedirects(response, reverse("iotcore:sequence_group_manage"))
        sequence.refresh_from_db()
        self.assertIsNone(sequence.group)

    def test_automation_list_supports_group_status_trigger_action_and_search(self):
        group = AutomationGroup.objects.create(name="시스템 관리", order=10)
        sequence = Sequence.objects.create(name="쿠키 갱신 시퀀스")
        automation = Automation.objects.create(
            name="유튜브 뮤직 쿠키 갱신",
            group=group,
            is_favorite=True,
            enabled=True,
        )
        AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=AutomationTrigger.TriggerType.MQTT_EVENT,
            config={"topic": "iotcore/agents/pi5/cookie"},
            enabled=True,
        )
        AutomationAction.objects.create(
            automation=automation,
            order=1,
            action_type=AutomationAction.ActionType.SEQUENCE,
            sequence=sequence,
        )
        Automation.objects.create(name="비활성 테스트", enabled=False)

        response = self.client.get(
            reverse("iotcore:automation_list"),
            {
                "scope": f"group:{group.pk}",
                "status": "enabled",
                "trigger": AutomationTrigger.TriggerType.MQTT_EVENT,
                "action": AutomationAction.ActionType.SEQUENCE,
                "q": "cookie",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, automation.name)
        self.assertNotContains(response, "비활성 테스트")
        self.assertContains(response, "시스템 관리")

    def test_deleting_automation_group_moves_members_to_ungrouped(self):
        group = AutomationGroup.objects.create(name="출퇴근", order=1)
        automation = Automation.objects.create(name="출근", group=group)

        response = self.client.post(
            reverse("iotcore:automation_group_manage"),
            {"action": "delete", "group_id": group.pk},
        )

        self.assertRedirects(response, reverse("iotcore:automation_group_manage"))
        automation.refresh_from_db()
        self.assertIsNone(automation.group)

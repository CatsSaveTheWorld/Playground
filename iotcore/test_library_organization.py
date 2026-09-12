from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Action, Automation, AutomationGroup, Device, Step, Trigger


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

    def _automation(self, name, automation_type, *, group=None, favorite=False, enabled=True):
        automation = Automation.objects.create(
            name=name,
            automation_type=automation_type,
            group=group,
            is_favorite=favorite,
            enabled=enabled,
        )
        step = Step.objects.create(automation=automation, order=1)
        Action.objects.create(
            step=step,
            order=1,
            action_type=Action.Type.DEVICE,
            device=self.device,
            function="power_on",
        )
        return automation, step

    def test_tabs_show_only_selected_automation_type(self):
        immediate, _ = self._automation("영화 모드", Automation.Type.IMMEDIATE)
        scheduled, step = self._automation("귀가 자동화", Automation.Type.SCHEDULED)
        Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.MQTT_EVENT,
            config={"topic": "zigbee2mqtt/livingroom_door_sensor", "field": "contact", "operator": "==", "value": False},
        )

        scheduled_response = self.client.get(reverse("iotcore:automation_list"))
        self.assertEqual(scheduled_response.status_code, 200)
        self.assertContains(scheduled_response, scheduled.name)
        self.assertNotContains(scheduled_response, immediate.name)
        self.assertContains(scheduled_response, "예약 실행")
        self.assertContains(scheduled_response, "즉시 실행")

        immediate_response = self.client.get(
            reverse("iotcore:automation_list"),
            {"type": Automation.Type.IMMEDIATE},
        )
        self.assertContains(immediate_response, immediate.name)
        self.assertNotContains(immediate_response, scheduled.name)

    def test_default_tab_is_scheduled(self):
        response = self.client.get(reverse("iotcore:automation_list"))
        self.assertTrue(response.context["is_scheduled"])
        self.assertFalse(response.context["is_immediate"])

    def test_scheduled_without_trigger_stays_scheduled(self):
        automation, _ = self._automation("트리거 없는 예약", Automation.Type.SCHEDULED)

        response = self.client.get(reverse("iotcore:automation_list"))
        self.assertContains(response, automation.name)

        automation.refresh_from_db()
        self.assertEqual(automation.automation_type, Automation.Type.SCHEDULED)

    def test_active_scheduled_automations_are_listed_first_inside_group(self):
        group = AutomationGroup.objects.create(name="집안 관리", order=1)
        disabled, disabled_step = self._automation(
            "비활성 예약", Automation.Type.SCHEDULED, group=group, enabled=False
        )
        enabled, enabled_step = self._automation(
            "활성 예약", Automation.Type.SCHEDULED, group=group, enabled=True
        )
        for step in (disabled_step, enabled_step):
            Trigger.objects.create(
                step=step,
                order=1,
                trigger_type=Trigger.Type.MQTT_EVENT,
                config={"topic": f"test/{step.pk}", "operator": "received"},
            )

        response = self.client.get(reverse("iotcore:automation_list"))
        content = response.content.decode("utf-8")

        self.assertLess(content.index(enabled.name), content.index(disabled.name))

    def test_empty_groups_are_rendered_in_group_order(self):
        media = AutomationGroup.objects.create(name="미디어", order=10)
        home = AutomationGroup.objects.create(name="집안 관리", order=20)
        self._automation("음악 재생", Automation.Type.IMMEDIATE, group=media)
        # 집안 관리 intentionally has no immediate Automation.

        response = self.client.get(
            reverse("iotcore:automation_list"),
            {"type": Automation.Type.IMMEDIATE},
        )
        content = response.content.decode("utf-8")

        self.assertContains(response, "미디어")
        self.assertContains(response, "집안 관리")
        self.assertContains(response, "음악 재생")
        self.assertContains(response, "이 그룹에 등록된 즉시 실행 항목이 없습니다.")
        self.assertLess(content.index("미디어"), content.index("집안 관리"))

        sections = response.context["group_sections"]
        self.assertEqual([section["name"] for section in sections[:2]], ["미디어", "집안 관리"])
        self.assertEqual(len(sections[0]["items"]), 1)
        self.assertEqual(len(sections[1]["items"]), 0)

    def test_group_is_visible_on_other_tab_even_when_it_has_no_items_there(self):
        media = AutomationGroup.objects.create(name="미디어", order=1)
        self._automation("한 번 재생", Automation.Type.IMMEDIATE, group=media)

        scheduled_response = self.client.get(reverse("iotcore:automation_list"))
        self.assertContains(scheduled_response, "미디어")
        self.assertContains(scheduled_response, "이 그룹에 등록된 예약 실행 항목이 없습니다.")

    def _update_payload(self, automation, *, automation_type=None):
        step = automation.steps.get(order=1)
        action = step.actions.get(order=1)
        data = {
            "name": automation.name,
            "description": automation.description,
            "automation_type": automation_type or automation.automation_type,
            "group": automation.group_id or "",
            "enabled": "on" if automation.enabled else "",
            "cooldown_seconds": str(automation.cooldown_seconds),
            "triggers-TOTAL_FORMS": "1",
            "triggers-INITIAL_FORMS": "1",
            "triggers-MIN_NUM_FORMS": "0",
            "triggers-MAX_NUM_FORMS": "1000",
            "triggers-0-id": str(step.pk),
            "triggers-0-set_key": f"step-{step.pk}",
            "triggers-0-enabled": "on",
            "triggers-0-condition_operator": step.trigger_operator,
            "conditions-TOTAL_FORMS": "0",
            "conditions-INITIAL_FORMS": "0",
            "conditions-MIN_NUM_FORMS": "0",
            "conditions-MAX_NUM_FORMS": "1000",
            "actions-TOTAL_FORMS": "1",
            "actions-INITIAL_FORMS": "1",
            "actions-MIN_NUM_FORMS": "0",
            "actions-MAX_NUM_FORMS": "1000",
            "actions-0-id": str(action.pk),
            "actions-0-trigger_key": f"step-{step.pk}",
            "actions-0-trigger_index": "0",
            "actions-0-action_type": Action.Type.DEVICE,
            "actions-0-device": str(self.device.pk),
            "actions-0-function": action.function,
            "actions-0-parameter_json": "",
            "actions-0-sequence": "",
            "actions-0-delay": str(action.delay),
            "actions-0-delay_position": action.delay_position,
        }
        if automation.is_favorite:
            data["is_favorite"] = "on"
        return data

    def _attach_device_state_trigger(self, automation, key="power"):
        step = automation.steps.get(order=1)
        trigger = Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={"device_id": self.device.pk, "key": key, "operator": "changed", "value": None},
        )
        return step, trigger

    def _payload_with_existing_trigger(self, automation, step, trigger, *, automation_type, delete=False):
        payload = self._update_payload(automation, automation_type=automation_type)
        payload.update({
            "conditions-TOTAL_FORMS": "1",
            "conditions-INITIAL_FORMS": "1",
            "conditions-0-id": str(trigger.pk),
            "conditions-0-trigger_key": f"step-{step.pk}",
            "conditions-0-trigger_index": "0",
            "conditions-0-condition_type": Trigger.Type.DEVICE_STATE,
            "conditions-0-state_device": str(self.device.pk),
            "conditions-0-state_key": trigger.config.get("key", "power"),
            "conditions-0-state_operator": "changed",
            "conditions-0-state_value": "",
        })
        if delete:
            payload["conditions-0-DELETE"] = "on"
        return payload

    def test_user_can_explicitly_move_immediate_to_scheduled(self):
        automation, step = self._automation("상태 감시", Automation.Type.IMMEDIATE)
        trigger = Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={"device_id": self.device.pk, "key": "power", "operator": "changed", "value": None},
        )
        payload = self._payload_with_existing_trigger(
            automation,
            step,
            trigger,
            automation_type=Automation.Type.SCHEDULED,
        )

        response = self.client.post(
            reverse("iotcore:automation_update", kwargs={"automation_id": automation.pk}),
            payload,
        )

        self.assertRedirects(
            response,
            f'{reverse("iotcore:automation_list")}?type={Automation.Type.SCHEDULED}',
        )
        automation.refresh_from_db()
        self.assertEqual(automation.automation_type, Automation.Type.SCHEDULED)

    def test_deleting_trigger_does_not_change_user_selected_type(self):
        automation, step = self._automation("문 열림 반복", Automation.Type.SCHEDULED)
        trigger = Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={"device_id": self.device.pk, "key": "contact", "operator": "changed", "value": None},
        )
        payload = self._payload_with_existing_trigger(
            automation,
            step,
            trigger,
            automation_type=Automation.Type.SCHEDULED,
            delete=True,
        )

        response = self.client.post(
            reverse("iotcore:automation_update", kwargs={"automation_id": automation.pk}),
            payload,
        )

        self.assertRedirects(
            response,
            f'{reverse("iotcore:automation_list")}?type={Automation.Type.SCHEDULED}',
        )
        automation.refresh_from_db()
        self.assertEqual(automation.automation_type, Automation.Type.SCHEDULED)
        self.assertFalse(automation.steps.get(order=1).triggers.exists())

    def test_user_can_explicitly_move_scheduled_to_immediate_with_trigger_intact(self):
        automation, step = self._automation("수동 조건 검사", Automation.Type.SCHEDULED)
        trigger = Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={"device_id": self.device.pk, "key": "power", "operator": "changed", "value": None},
        )
        payload = self._payload_with_existing_trigger(
            automation,
            step,
            trigger,
            automation_type=Automation.Type.IMMEDIATE,
        )

        response = self.client.post(
            reverse("iotcore:automation_update", kwargs={"automation_id": automation.pk}),
            payload,
        )

        self.assertRedirects(
            response,
            f'{reverse("iotcore:automation_list")}?type={Automation.Type.IMMEDIATE}',
        )
        automation.refresh_from_db()
        self.assertEqual(automation.automation_type, Automation.Type.IMMEDIATE)
        self.assertTrue(automation.steps.get(order=1).triggers.exists())

    def test_user_can_make_triggerless_automation_scheduled(self):
        automation, _ = self._automation("대기용 예약", Automation.Type.IMMEDIATE)
        payload = self._update_payload(
            automation,
            automation_type=Automation.Type.SCHEDULED,
        )

        response = self.client.post(
            reverse("iotcore:automation_update", kwargs={"automation_id": automation.pk}),
            payload,
        )

        self.assertRedirects(
            response,
            f'{reverse("iotcore:automation_list")}?type={Automation.Type.SCHEDULED}',
        )
        automation.refresh_from_db()
        self.assertEqual(automation.automation_type, Automation.Type.SCHEDULED)

    def test_old_sequence_list_redirects_to_immediate_tab(self):
        response = self.client.get(reverse("iotcore:sequence_list"))
        self.assertRedirects(
            response,
            f'{reverse("iotcore:automation_list")}?type={Automation.Type.IMMEDIATE}',
        )

    def test_favorite_toggle_works_for_immediate_automation(self):
        automation, _ = self._automation("즐겨찾기 테스트", Automation.Type.IMMEDIATE)
        list_url = reverse("iotcore:automation_list")

        response = self.client.post(
            reverse("iotcore:automation_favorite_toggle", kwargs={"automation_id": automation.pk}),
            {"next": list_url},
        )

        self.assertRedirects(response, list_url)
        automation.refresh_from_db()
        self.assertTrue(automation.is_favorite)

    def test_deleting_group_moves_both_types_to_ungrouped(self):
        group = AutomationGroup.objects.create(name="삭제할 그룹", order=1)
        immediate, _ = self._automation("즉시", Automation.Type.IMMEDIATE, group=group)
        scheduled, _ = self._automation("예약", Automation.Type.SCHEDULED, group=group)

        response = self.client.post(
            reverse("iotcore:automation_group_manage"),
            {"action": "delete", "group_id": group.pk},
        )

        self.assertRedirects(response, reverse("iotcore:automation_group_manage"))
        immediate.refresh_from_db()
        scheduled.refresh_from_db()
        self.assertIsNone(immediate.group)
        self.assertIsNone(scheduled.group)

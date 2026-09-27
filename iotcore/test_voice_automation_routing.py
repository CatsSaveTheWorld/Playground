from django.test import TestCase

from .ai.service import AIControlService
from .forms import AutomationForm
from .models import Automation


class VoiceAutomationRoutingTests(TestCase):
    def test_matches_automation_name_before_device_parser(self):
        automation = Automation.objects.create(
            name="에어컨 냉방 실행",
            automation_type=Automation.Type.IMMEDIATE,
            enabled=True,
        )

        resolved = AIControlService.resolve_automation_from_text(
            "에어컨 냉방 실행해줘"
        )

        self.assertEqual(resolved, automation)

    def test_matches_alias_ignoring_spaces(self):
        automation = Automation.objects.create(
            name="빔 프로젝터 파이 진입",
            voice_aliases=["프로젝터 파이", "파이 진입"],
            automation_type=Automation.Type.IMMEDIATE,
            enabled=True,
        )

        resolved = AIControlService.resolve_automation_from_text(
            "빔프로젝터 파이진입 해줘"
        )

        self.assertEqual(resolved, automation)

    def test_prefers_longer_matching_automation(self):
        Automation.objects.create(
            name="영화",
            automation_type=Automation.Type.IMMEDIATE,
            enabled=True,
        )
        specific = Automation.objects.create(
            name="영화 모드",
            automation_type=Automation.Type.IMMEDIATE,
            enabled=True,
        )

        resolved = AIControlService.resolve_automation_from_text("영화 모드 실행")

        self.assertEqual(resolved, specific)

    def test_disabled_automation_is_not_matched(self):
        Automation.objects.create(
            name="게임 모드",
            automation_type=Automation.Type.IMMEDIATE,
            enabled=False,
        )

        self.assertIsNone(AIControlService.resolve_automation_from_text("게임 모드"))

    def test_form_parses_aliases(self):
        form = AutomationForm(
            data={
                "name": "게임 모드",
                "description": "",
                "voice_aliases": "게임 세팅, 게임 설정\n게임 준비",
                "automation_type": Automation.Type.IMMEDIATE,
                "group": "",
                "is_favorite": "",
                "enabled": "on",
                "cooldown_seconds": 0,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["voice_aliases"],
            ["게임 세팅", "게임 설정", "게임 준비"],
        )

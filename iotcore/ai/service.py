"""Stable service boundary for optional AI clients.

AI is deliberately a caller of IoTCore, never an alternative execution engine.
All device commands and Automation runs pass through the same deterministic
services used by the web UI, scheduler and MQTT listener.
"""
import re
import unicodedata
from types import SimpleNamespace

from django.core.exceptions import ValidationError

from ..device.services.device_service import DeviceService
from .pc_actions import PC_AI_ACTIONS
from ..device_actions import DeviceActionRegistry
from ..models import Automation, AutomationRun, Device
from ..scheduler.executor import AutomationExecutor


class AIControlService:
    @staticmethod
    def resolve_device(*, device_uid=None, device_type=None, location=None):
        if device_uid:
            return Device.objects.filter(device_uid=device_uid).first()

        if device_type and location:
            device = Device.objects.filter(
                device_type=device_type,
                location=location,
            ).first()
            if device:
                return device

        if device_type:
            devices = Device.objects.filter(device_type=device_type)
            if devices.count() == 1:
                return devices.first()

        return None

    @staticmethod
    def normalize_voice_text(value):
        text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
        text = re.sub(r"[,.!?~]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def resolve_automation_from_text(cls, text):
        """Resolve an enabled Automation by DB name/voice aliases.

        Matching is whitespace-insensitive and prefers an exact/longer phrase,
        so a routine name such as ``에어컨 냉방 실행`` wins before NLP can
        reduce the same utterance to a single device action such as mode_cool.
        """
        normalized_text = cls.normalize_voice_text(text)
        compact_text = normalized_text.replace(" ", "")
        if not compact_text:
            return None

        matches = []
        for automation in Automation.objects.filter(enabled=True).only(
            "id", "name", "voice_aliases"
        ):
            phrases = [automation.name]
            aliases = automation.voice_aliases or []
            if isinstance(aliases, (list, tuple)):
                phrases.extend(aliases)

            for phrase in phrases:
                normalized_phrase = cls.normalize_voice_text(phrase)
                compact_phrase = normalized_phrase.replace(" ", "")
                if not compact_phrase:
                    continue
                if compact_phrase not in compact_text:
                    continue
                exact = compact_phrase == compact_text
                matches.append((exact, len(compact_phrase), automation.id, automation))
                break

        if not matches:
            return None

        matches.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
        return matches[0][3]

    @staticmethod
    def resolve_automation(*, automation_name=None):
        if not automation_name:
            return None

        return Automation.objects.filter(
            name=automation_name,
            enabled=True,
        ).first()

    @staticmethod
    def execute_device_action(*, device, function, parameter=None):
        if function in PC_AI_ACTIONS:
            raise ValidationError('AI 모델 배치 전환은 기기 제어 또는 등록된 자동화에서 실행하세요.')
        supported = {action.code for action in DeviceActionRegistry.get_actions(device.device_type)}
        if function not in supported:
            raise ValidationError(
                f"{device.name}에서 지원하지 않는 동작입니다: {function}"
            )
        action = SimpleNamespace(
            device=device,
            function=function,
            parameter=parameter or {},
        )
        return DeviceService.execute_step(action)

    @staticmethod
    def run_automation(automation_id):
        automation = Automation.objects.filter(pk=automation_id, enabled=True).first()
        if automation is None:
            raise ValidationError("실행 가능한 자동화를 찾을 수 없습니다.")
        return AutomationExecutor.enqueue(
            automation,
            source=AutomationRun.Source.AI,
        )

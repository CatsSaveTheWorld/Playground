"""Stable service boundary for optional AI clients.

AI is deliberately a caller of IoTCore, never an alternative execution engine.
All device commands and Automation runs pass through the same deterministic
services used by the web UI, scheduler and MQTT listener.
"""
from types import SimpleNamespace

from django.core.exceptions import ValidationError

from ..device.services.device_service import DeviceService
from ..device_actions import DeviceActionRegistry
from ..models import Automation, AutomationRun, Device
from ..scheduler.executor import AutomationExecutor


class AIControlService:
    @staticmethod
    def resolve_device(*, device_uid=None, device_type=None, location=None):
        if device_uid:
            return Device.objects.filter(device_uid=device_uid).first()
        if device_type and location:
            return Device.objects.filter(device_type=device_type, location=location).first()
        return None

    @staticmethod
    def execute_device_action(*, device, function, parameter=None):
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

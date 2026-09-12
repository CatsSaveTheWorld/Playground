from django.contrib import admin

from .models import (
    Action,
    ActionRun,
    Automation,
    AutomationGroup,
    AutomationRun,
    Controller,
    Device,
    DeviceState,
    DoorEvent,
    NodeMetricSample,
    Step,
    Trigger,
)

for model in (
    Device, Controller, AutomationGroup, Automation, Step, Trigger, Action,
    AutomationRun, ActionRun, DeviceState, DoorEvent, NodeMetricSample,
):
    admin.site.register(model)

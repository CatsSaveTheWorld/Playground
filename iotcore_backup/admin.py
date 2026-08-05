from django.contrib import admin
from .models import (
    Automation,
    AutomationCondition,
    AutomationTrigger,
    Controller,
    Device,
    DeviceState,
    Sequence,
    SequenceRun,
    SequenceStep,
    SequenceStepRun,
)

admin.site.register(Controller)
admin.site.register(Device)
admin.site.register(Sequence)
admin.site.register(SequenceStep)
admin.site.register(Automation)
admin.site.register(AutomationTrigger)
admin.site.register(AutomationCondition)
admin.site.register(DeviceState)
admin.site.register(SequenceRun)
admin.site.register(SequenceStepRun)

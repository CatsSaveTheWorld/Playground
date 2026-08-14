from django.contrib import admin
from .models import (
    Automation,
    AutomationAction,
    AutomationActionRun,
    AutomationCondition,
    AutomationRun,
    AutomationTrigger,
    Controller,
    Device,
    DeviceState,
    NodeMetricSample,
    Sequence,
    SequenceRun,
    SequenceStep,
    SequenceStepRun,
)

admin.site.register(Controller)
@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "device_type",
        "device_role",
        "protocol",
        "device_uid",
        "location",
    )
    list_filter = ("device_role", "protocol", "device_type", "location")
    search_fields = ("name", "device_uid", "device_type", "location")

admin.site.register(Sequence)
admin.site.register(SequenceStep)
admin.site.register(Automation)
admin.site.register(AutomationAction)
admin.site.register(AutomationRun)
admin.site.register(AutomationActionRun)
admin.site.register(AutomationTrigger)
admin.site.register(AutomationCondition)
admin.site.register(DeviceState)
admin.site.register(NodeMetricSample)
admin.site.register(SequenceRun)
admin.site.register(SequenceStepRun)

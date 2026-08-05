import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ...device_actions import DeviceActionRegistry
from ...forms import (
    AutomationActionFormSet,
    AutomationConditionFormSet,
    AutomationForm,
    AutomationTriggerFormSet,
)
from ...models import (
    Automation,
    AutomationAction,
    AutomationCondition,
    AutomationTrigger,
    Device,
)
from ...scheduler.calculator import describe_trigger
from ...scheduler.service import AutomationService


def _automation_forms(request, automation):
    data = request.POST if request.method == "POST" else None
    return (
        AutomationForm(data, instance=automation),
        AutomationTriggerFormSet(data, instance=automation, prefix="triggers"),
        AutomationConditionFormSet(data, instance=automation, prefix="conditions"),
        AutomationActionFormSet(data, instance=automation, prefix="actions"),
    )


def _replace_children(automation, trigger_formset, condition_formset, action_formset):
    automation.triggers.all().delete()
    for form in trigger_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("trigger_type"):
            continue
        enabled = cleaned.get("enabled", True)
        # Dynamically added checkbox controls can be omitted by the browser.
        # A new trigger must be active by default; the automation-level toggle
        # remains the user-facing way to pause execution.
        if not form.instance.pk and form.add_prefix("enabled") not in form.data:
            enabled = True
        trigger = AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=cleaned["trigger_type"],
            config=cleaned["config"],
            enabled=enabled,
        )
        AutomationService.recalculate_trigger(trigger)

    automation.conditions.all().delete()
    order = 1
    for form in condition_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("condition_type"):
            continue
        AutomationCondition.objects.create(
            automation=automation,
            condition_type=cleaned["condition_type"],
            config=cleaned["config"],
            order=order,
        )
        order += 1

    automation.actions.all().delete()
    order = 1
    for form in action_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("action_type"):
            continue
        AutomationAction.objects.create(
            automation=automation,
            order=order,
            action_type=cleaned["action_type"],
            device=cleaned.get("device"),
            function=cleaned.get("function") or "",
            parameter=cleaned.get("parameter"),
            sequence=cleaned.get("sequence"),
            delay=cleaned.get("delay") or 0,
        )
        order += 1


def _action_ui_context():
    action_registry = {
        device_type: [
            {
                "code": action.code,
                "name": action.display_name,
                "parameter_key": action.parameter_key,
            }
            for action in actions
        ]
        for device_type, actions in DeviceActionRegistry._ACTIONS.items()
    }
    device_types = {
        str(device.pk): device.device_type
        for device in Device.objects.all()
    }
    return {
        "action_registry_json": json.dumps(action_registry, ensure_ascii=False),
        "device_types_json": json.dumps(device_types, ensure_ascii=False),
    }


def _render_form(request, context):
    context.update(_action_ui_context())
    return render(request, "iotcore/schedule_form.html", context)


@login_required(login_url="common:login")
def schedule_list(request):
    automations = list(
        Automation.objects
        .prefetch_related("triggers", "actions__device", "actions__sequence")
    )
    for automation in automations:
        triggers = list(automation.triggers.all())
        automation.summary = " / ".join(
            describe_trigger(trigger) for trigger in triggers
        ) or "실행 계기 없음"
        next_runs = [
            trigger.next_run_at for trigger in triggers
            if trigger.next_run_at is not None
        ]
        automation.next_run_at = min(next_runs) if next_runs else None

        action_summaries = []
        for action in automation.actions.all():
            if action.action_type == AutomationAction.ActionType.SEQUENCE:
                label = action.sequence.name if action.sequence else "-"
            else:
                device_name = action.device.name if action.device else "-"
                device_type = action.device.device_type if action.device else ""
                function_name = DeviceActionRegistry.get_display_name(
                    device_type,
                    action.function,
                )
                label = f"{device_name}: {function_name}"
            if action.delay:
                label += f" ({action.delay}초 후)"
            action_summaries.append(label)
        automation.action_summary = " / ".join(action_summaries) or "실행 동작 없음"

    return render(
        request,
        "iotcore/schedule_list.html",
        {"automations": automations},
    )


@login_required(login_url="common:login")
def schedule_create(request):
    automation = Automation()
    form, trigger_formset, condition_formset, action_formset = _automation_forms(
        request, automation
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and trigger_formset.is_valid()
        and condition_formset.is_valid()
        and action_formset.is_valid()
    ):
        with transaction.atomic():
            automation = form.save()
            _replace_children(
                automation,
                trigger_formset,
                condition_formset,
                action_formset,
            )
        messages.success(request, "자동화를 생성했습니다.")
        return redirect("iotcore:schedule_list")
    return _render_form(request, {
        "form": form,
        "trigger_formset": trigger_formset,
        "condition_formset": condition_formset,
        "action_formset": action_formset,
        "is_update": False,
    })


@login_required(login_url="common:login")
def schedule_update(request, schedule_id):
    automation = get_object_or_404(Automation, pk=schedule_id)
    form, trigger_formset, condition_formset, action_formset = _automation_forms(
        request, automation
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and trigger_formset.is_valid()
        and condition_formset.is_valid()
        and action_formset.is_valid()
    ):
        with transaction.atomic():
            automation = form.save()
            _replace_children(
                automation,
                trigger_formset,
                condition_formset,
                action_formset,
            )
        messages.success(request, "자동화를 수정했습니다.")
        return redirect("iotcore:schedule_list")
    return _render_form(request, {
        "form": form,
        "trigger_formset": trigger_formset,
        "condition_formset": condition_formset,
        "action_formset": action_formset,
        "automation": automation,
        "is_update": True,
    })


@login_required(login_url="common:login")
@require_POST
def schedule_toggle(request, schedule_id):
    automation = get_object_or_404(Automation, pk=schedule_id)
    automation.enabled = not automation.enabled
    automation.save(update_fields=["enabled", "updated_at"])
    AutomationService.recalculate_automation(automation)
    messages.success(
        request,
        f"자동화를 {'활성화' if automation.enabled else '비활성화'}했습니다.",
    )
    return redirect("iotcore:schedule_list")


@login_required(login_url="common:login")
@require_POST
def schedule_delete(request, schedule_id):
    automation = get_object_or_404(Automation, pk=schedule_id)
    automation.delete()
    messages.success(request, "자동화를 삭제했습니다.")
    return redirect("iotcore:schedule_list")

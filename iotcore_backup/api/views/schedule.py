from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ...forms import (
    AutomationConditionFormSet,
    AutomationForm,
    AutomationTriggerFormSet,
)
from ...models import Automation, AutomationCondition, AutomationTrigger
from ...scheduler.calculator import describe_trigger
from ...scheduler.service import AutomationService


def _automation_forms(request, automation):
    data = request.POST if request.method == "POST" else None
    return (
        AutomationForm(data, instance=automation),
        AutomationTriggerFormSet(
            data,
            instance=automation,
            prefix="triggers",
        ),
        AutomationConditionFormSet(
            data,
            instance=automation,
            prefix="conditions",
        ),
    )


def _replace_children(automation, trigger_formset, condition_formset):
    automation.triggers.all().delete()
    for form in trigger_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE"):
            continue
        trigger = AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=cleaned["trigger_type"],
            config=cleaned["config"],
            enabled=cleaned.get("enabled", True),
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


@login_required(login_url="common:login")
def schedule_list(request):
    automations = list(
        Automation.objects
        .select_related("sequence")
        .prefetch_related("triggers")
    )
    for automation in automations:
        triggers = list(automation.triggers.all())
        automation.summary = " / ".join(
            describe_trigger(trigger) for trigger in triggers
        ) or "실행 계기 없음"
        next_runs = [
            trigger.next_run_at
            for trigger in triggers
            if trigger.next_run_at is not None
        ]
        automation.next_run_at = min(next_runs) if next_runs else None
    return render(
        request,
        "iotcore/schedule_list.html",
        {"automations": automations},
    )


@login_required(login_url="common:login")
def schedule_create(request):
    automation = Automation()
    form, trigger_formset, condition_formset = _automation_forms(
        request,
        automation,
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and trigger_formset.is_valid()
        and condition_formset.is_valid()
    ):
        with transaction.atomic():
            automation = form.save()
            _replace_children(automation, trigger_formset, condition_formset)
        messages.success(request, "자동화를 생성했습니다.")
        return redirect("iotcore:schedule_list")
    return render(
        request,
        "iotcore/schedule_form.html",
        {
            "form": form,
            "trigger_formset": trigger_formset,
            "condition_formset": condition_formset,
            "is_update": False,
        },
    )


@login_required(login_url="common:login")
def schedule_update(request, schedule_id):
    automation = get_object_or_404(Automation, pk=schedule_id)
    form, trigger_formset, condition_formset = _automation_forms(
        request,
        automation,
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and trigger_formset.is_valid()
        and condition_formset.is_valid()
    ):
        with transaction.atomic():
            automation = form.save()
            _replace_children(automation, trigger_formset, condition_formset)
        messages.success(request, "자동화를 수정했습니다.")
        return redirect("iotcore:schedule_list")
    return render(
        request,
        "iotcore/schedule_form.html",
        {
            "form": form,
            "trigger_formset": trigger_formset,
            "condition_formset": condition_formset,
            "automation": automation,
            "is_update": True,
        },
    )


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

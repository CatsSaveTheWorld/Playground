"""Backward-compatible routes for the removed Sequence model.

Sequence is now Automation(automation_type='immediate').  Old bookmarks/routes
stay valid, but all real CRUD/execution lives in api.views.automation.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from ...models import Automation
from . import automation as automation_views


@login_required(login_url="common:login")
def sequence_list(request):
    return redirect(f"{reverse('iotcore:automation_list')}?type={Automation.Type.IMMEDIATE}")


@login_required(login_url="common:login")
def sequence_create(request):
    # Preserve POSTs from old links while selecting the new internal type.
    if request.method == "GET":
        query = request.GET.copy()
        query["type"] = Automation.Type.IMMEDIATE
        request.GET = query
    else:
        data = request.POST.copy()
        data["automation_type"] = Automation.Type.IMMEDIATE
        request.POST = data
    return automation_views.automation_create(request)


@login_required(login_url="common:login")
def sequence_edit(request, sequence_id):
    get_object_or_404(Automation, pk=sequence_id, automation_type=Automation.Type.IMMEDIATE)
    return automation_views.automation_update(request, sequence_id)


sequence_update = sequence_edit


@login_required(login_url="common:login")
@require_POST
def sequence_run(request, sequence_id):
    get_object_or_404(Automation, pk=sequence_id, automation_type=Automation.Type.IMMEDIATE)
    return automation_views.automation_run(request, sequence_id)


@login_required(login_url="common:login")
@require_POST
def sequence_delete(request, sequence_id):
    get_object_or_404(Automation, pk=sequence_id, automation_type=Automation.Type.IMMEDIATE)
    return automation_views.automation_delete(request, sequence_id)


@login_required(login_url="common:login")
@require_POST
def sequence_favorite_toggle(request, sequence_id):
    return automation_views.automation_favorite_toggle(request, sequence_id)


@login_required(login_url="common:login")
def sequence_group_manage(request):
    return automation_views.automation_group_manage(request)


# Legacy step endpoints are no longer independent resources. Editing happens
# atomically through the unified Automation editor.
def sequence_step_create(request, sequence_id):
    return sequence_edit(request, sequence_id)


def sequence_step_delete(request):
    return redirect("iotcore:automation_list")

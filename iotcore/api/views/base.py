from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET
from ...execution_history import history_context, execution_detail_context
from ...models import AutomationRun
from django.urls import reverse

from ...models import Automation, Device, DeviceState
from ...monitoring.service import NodeTelemetryService
from ...monitoring.inference import AIInferenceMonitor
from ...monitoring.routing import routing_summary
from ...room_entry.service import RoomEntryService
from ...weather.service import KmaWeatherService


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            if user.is_staff:
                return redirect("iotcore:dashboard")
            messages.error(request, "접근 권한이 없습니다.")
            return redirect("common:login")
        return render(request, "iotcore/login.html", {"error": True})
    return render(request, "iotcore/login.html")


def logout_view(request):
    logout(request)
    return redirect("iotcore:login_view")


def staff_check(user):
    return user.is_staff


def ai_node_snapshot(*, uid=None, history_seconds=None, routes=None):
    uid = uid or getattr(settings, "IOTCORE_AI_NODE_UID", "home-ai-main")
    node = NodeTelemetryService.snapshot(uid, history_seconds=history_seconds)
    node["ai"] = AIInferenceMonitor.snapshot(node)
    node["ai"]["routing"] = routing_summary() if routes is None else routes
    return node


def ai_node_snapshots(*, history_seconds=None):
    preferred = getattr(settings, "IOTCORE_AI_NODE_UID", "home-ai-main")
    registered = set(Device.objects.filter(device_type='pc').values_list('device_uid', flat=True))
    uids = list(dict.fromkeys(uid for uid in (preferred, 'home-ai-main', 'home-ai-sub1')
                             if uid == preferred or uid in registered))
    routes = routing_summary()
    return {uid: ai_node_snapshot(uid=uid, history_seconds=history_seconds, routes=routes) for uid in uids}


@login_required(login_url="common:login")
@user_passes_test(staff_check, login_url="common:login")
def ai_monitor(request):
    nodes = ai_node_snapshots(history_seconds=60)
    return render(request, "iotcore/ai_monitor.html", {
        "ai_node": next(iter(nodes.values())),
        "ai_nodes": list(nodes.values()),
        "node_snapshots": nodes,
        "node_metrics_url": reverse("iotcore:dashboard_node_metrics"),
    })


@login_required(login_url="common:login")
@user_passes_test(staff_check, login_url="common:login")
def dashboard(request):
    sensor = Device.objects.filter(
        device_uid="leedowon_room_temp_humidity"
    ).first()

    environment = None

    if sensor is not None:
        topic = f"zigbee2mqtt/{sensor.device_uid}"

        rows = list(
            DeviceState.objects.filter(
                topic=topic,
                key__in=[
                    "temperature",
                    "humidity",
                    "pressure",
                    "battery",
                ],
            )
        )

        states = {row.key: row for row in rows}

        def get_value(key):
            state = states.get(key)
            return state.value if state is not None else None

        environment = {
            "device": sensor,
            "temperature": get_value("temperature"),
            "humidity": get_value("humidity"),
            "pressure": get_value("pressure"),
            "battery": get_value("battery"),
            "updated_at": max(
                (row.updated_at for row in rows),
                default=None,
            ),
        }

    ai_nodes = ai_node_snapshots(history_seconds=60)
    pi5_uid = getattr(settings, "IOTCORE_PI5_NODE_UID", "pi5")

    context = {
        "environment": environment,
        "entry_status": RoomEntryService.snapshot(),
        "weather": KmaWeatherService.snapshot(),
        "ai_node": next(iter(ai_nodes.values())),
        "ai_nodes": list(ai_nodes.values()),
        "pi5_node": NodeTelemetryService.snapshot(pi5_uid),
        "ai_control_url": reverse("iotcore:ai_monitor"),
        "node_metrics_url": reverse("iotcore:dashboard_node_metrics"),
    }

    context["node_snapshots"] = {**ai_nodes, context["pi5_node"]["uid"]: context["pi5_node"]}

    return render(request, "iotcore/dashboard.html", context)


@login_required(login_url="common:login")
@user_passes_test(staff_check, login_url="common:login")
def dashboard_node_metrics(request):
    ai_nodes = ai_node_snapshots(history_seconds=60)
    pi5_uid = getattr(settings, "IOTCORE_PI5_NODE_UID", "pi5")
    return JsonResponse(
        {
            "entry_status": RoomEntryService.snapshot(),
            "weather": KmaWeatherService.snapshot(),
            "nodes": {
                **ai_nodes,
                pi5_uid: NodeTelemetryService.snapshot(
                    pi5_uid,
                    history_seconds=60,
                ),
            }
        }
    )


@login_required(login_url="common:login")
@require_GET
def execution_history(request):
    return render(request, "iotcore/execution_history.html", history_context(request))


@login_required(login_url="common:login")
@require_GET
def execution_history_detail(request, run_id):
    run = get_object_or_404(AutomationRun, pk=run_id, root_run__isnull=True, parent_action_run__isnull=True)
    return render(request, "iotcore/execution_history_detail.html", execution_detail_context(run, request.GET))


@login_required(login_url="common:login")
def settings_page(request):
    return render(request, "iotcore/settings.html")

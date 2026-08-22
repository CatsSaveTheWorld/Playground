from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from ...models import Automation, Device, DeviceState, Sequence
from ...monitoring.service import NodeTelemetryService
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

    ai_uid = getattr(settings, "IOTCORE_AI_NODE_UID", "home-ai-main")
    pi5_uid = getattr(settings, "IOTCORE_PI5_NODE_UID", "pi5")

    context = {
        "environment": environment,
        "entry_status": RoomEntryService.snapshot(),
        "weather": KmaWeatherService.snapshot(),
        "ai_node": NodeTelemetryService.snapshot(ai_uid),
        "pi5_node": NodeTelemetryService.snapshot(pi5_uid),
        "ai_control_url": reverse("iotcore:automation_list"),
        "node_metrics_url": reverse("iotcore:dashboard_node_metrics"),
    }

    return render(request, "iotcore/dashboard.html", context)


@login_required(login_url="common:login")
@user_passes_test(staff_check, login_url="common:login")
def dashboard_node_metrics(request):
    ai_uid = getattr(settings, "IOTCORE_AI_NODE_UID", "home-ai-main")
    pi5_uid = getattr(settings, "IOTCORE_PI5_NODE_UID", "pi5")
    return JsonResponse(
        {
            "entry_status": RoomEntryService.snapshot(),
            "weather": KmaWeatherService.snapshot(),
            "nodes": {
                ai_uid: NodeTelemetryService.snapshot(
                    ai_uid,
                    history_seconds=60,
                ),
                pi5_uid: NodeTelemetryService.snapshot(
                    pi5_uid,
                    history_seconds=60,
                ),
            }
        }
    )


@login_required(login_url="common:login")
def execution_history(request):
    return render(request, "iotcore/execution_history.html")


@login_required(login_url="common:login")
def settings_page(request):
    return render(request, "iotcore/settings.html")

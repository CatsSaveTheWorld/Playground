from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render

from ...models import Automation, Device, Sequence


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
    context = {
        "device_count": Device.objects.count(),
        "sequence_count": Sequence.objects.count(),
        "automation_count": Automation.objects.count(),
        "enabled_automation_count": Automation.objects.filter(enabled=True).count(),
    }
    return render(request, "iotcore/dashboard.html", context)


@login_required(login_url="common:login")
def execution_history(request):
    return render(request, "iotcore/execution_history.html")


@login_required(login_url="common:login")
def settings_page(request):
    return render(request, "iotcore/settings.html")

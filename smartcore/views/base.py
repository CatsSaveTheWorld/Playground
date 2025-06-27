from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
# from ..forms import QuestionForm
# from ..models import Question

# Create your views here.
def login_view(request):
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("smartcore:dashboard")
        else:
            return render(request, "smartcore/login.html", {'error' : True})
    return render("smartcore/login.html")


def logout_view(request):
    logout(request)
    return redirect('smartcore:login_view')


@login_required(login_url="common:login")
def dashboard(request):
    return render(request, "smartcore/dashboard.html")



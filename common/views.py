from django.shortcuts import redirect, render
from django.contrib.auth import authenticate, login, logout
from common.forms import UserForm
from django.contrib.auth.decorators import login_required


# Create your views here.
def logout_view(request):
    logout(request)
    return redirect('board:index')


def signup(request):
    if request.method == "POST":
        form = UserForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get('username')
            raw_password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=raw_password)
            if user is not None:
                login(request, user)
                return redirect('board:index')
            else:
                form.add_error(None, "Authentication failed. Please check your credentials.")
    else:
        form = UserForm()
    return render(request, "common/signup.html", {'form': form})


def some_view(request):
    context = {
        'has_iot_permission' : request.user.has_perm('smartcore.can_control_iot_devices')
    }
    return render(request, 'your_template.html', context)

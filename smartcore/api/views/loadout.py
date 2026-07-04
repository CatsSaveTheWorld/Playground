from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
# from ..forms import QuestionForm
# from ..models import Question

# Create your views here.
def loadout_list(request):
    return render(request, "smartcore/loadout_list.html")



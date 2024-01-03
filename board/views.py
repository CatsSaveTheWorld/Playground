from django.shortcuts import render, redirect

# Create your views here.


def index(request):
    return render(request, "index.html")


def portfolio(request):
    return render(request, "portfolio.html")

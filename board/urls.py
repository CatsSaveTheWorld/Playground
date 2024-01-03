from django.contrib import admin
from django.urls import path, include
from . import views

app_name = "board"

urlpatterns = [
    path("", views.index, name="index"),
    path("index", views.index, name="index"),
    path("portfolio", views.portfolio, name="portfolio"),
    # path("board/", ),
]

from django.urls import path
from smartcore.views import *


app_name = "smartcore"

urlpatterns = [
    path("", base.dashboard, name="dashboard"),
    path("login", base.dashboard, name="login_view"),
    path("dashboard/", base.dashboard, name="dashboard"),
    path("logout/", base.logout_view, name="logout_view"),
    path("loadout/", loadout.loadout_list, name="loadout_list"),
    path("detail/", detail.detail_list, name="detail_list"),
    # path("<int:question_id>/", base_views.detail, name="detail"),

    # 에어컨 관련
    path('aircon/power_on/', detail.aircon_power_on, name='aircon_power_on'),
    path('aircon/power_off/', detail.aircon_power_off, name='aircon_power_off'),
    path('aircon/set_temp/', detail.aircon_set_temp, name='aircon_set_temp'),
    path('aircon/mode_auto/', detail.aircon_mode_auto, name='aircon_mode_auto'),
    path('aircon/mode_cool/', detail.aircon_mode_cool, name='aircon_mode_cool'),
    path('aircon/mode_dehumidification/', detail.aircon_dehumidification_mode, name='aircon_dehumidification_mode'),
    path('aircon/mode_fan/', detail.aircon_mode_fan, name='aircon_mode_fan'),
]

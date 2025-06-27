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
    path('aircon/power_on', detail.aircon_power_on, name='aircon_power_on'),
    path('aircon/power_off', detail.aircon_power_off, name='aircon_power_off'),
]

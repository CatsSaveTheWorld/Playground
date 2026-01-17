from django.urls import path
from smartcore.views import base, loadout, detail

app_name = "smartcore"

urlpatterns = [
    # ────────────────────────────────
    #  기본 페이지 / 인증 관련
    # ────────────────────────────────
    path("", base.dashboard, name="dashboard"),
    path("login/", base.dashboard, name="login_view"),
    path("dashboard/", base.dashboard, name="dashboard"),
    path("logout/", base.logout_view, name="logout_view"),

    # ────────────────────────────────
    #  페이지별 View
    # ────────────────────────────────
    path("loadout/", loadout.loadout_list, name="loadout_list"),
    path("detail/", detail.detail_list, name="detail_list"),

    # ────────────────────────────────
    #  AI 통합 제어 엔드포인트 (AI PC → Django)
    # ────────────────────────────────
    path("ai_control/", detail.ai_control, name="ai_control"),

    # ────────────────────────────────
    #  에어컨 제어 (웹페이지 → Django)
    # ────────────────────────────────
    path("aircon/power_on/", detail.aircon_power_on, name="aircon_power_on"),
    path("aircon/power_off/", detail.aircon_power_off, name="aircon_power_off"),
    path("aircon/set_temp/", detail.aircon_set_temp, name="aircon_set_temp"),
    path("aircon/mode_auto/", detail.aircon_mode_auto, name="aircon_mode_auto"),
    path("aircon/mode_cool/", detail.aircon_mode_cool, name="aircon_mode_cool"),
    path("aircon/mode_dehumidification/", detail.aircon_dehumidification_mode, name="aircon_mode_dehumidification"),
    path("aircon/mode_fan/", detail.aircon_mode_fan, name="aircon_mode_fan"),

    # ────────────────────────────────
    #  PC 제어
    # ────────────────────────────────
    path("pc/power_on/", detail.pc_power_on, name="pc_power_on"),
    path("pc/power_off/", detail.pc_power_off, name="pc_power_off"),

]

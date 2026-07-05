from django.urls import path
from iotcore.api.views import base, sequence, detail
from iotcore.api.views import aircon, pc, electric_fan, ai

app_name = "iotcore"

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
    path("sequence/", sequence.sequence_list, name="sequence_list"),
    path("detail/", detail.detail_list, name="detail_list"),

    # ────────────────────────────────
    #  AI 통합 제어 엔드포인트 (AI PC → Django)
    # ────────────────────────────────
    path("ai_control/", ai.ai_control, name="ai_control"),

    # ────────────────────────────────
    #  에어컨 제어 (웹페이지 → Django)
    # ────────────────────────────────
    path("aircon/power_on/", aircon.aircon_power_on, name="aircon_power_on"),
    path("aircon/power_off/", aircon.aircon_power_off, name="aircon_power_off"),
    path("aircon/set_temp/", aircon.aircon_set_temp, name="aircon_set_temp"),
    path("aircon/mode_auto/", aircon.aircon_mode_auto, name="aircon_mode_auto"),
    path("aircon/mode_cool/", aircon.aircon_mode_cool, name="aircon_mode_cool"),
    path("aircon/mode_dehumidification/", aircon.aircon_dehumidification_mode, name="aircon_mode_dehumidification"),
    path("aircon/mode_fan/", aircon.aircon_mode_fan, name="aircon_mode_fan"),

    # ────────────────────────────────
    #  PC 제어
    # ────────────────────────────────
    path("pc/power_on/", pc.pc_power_on, name="pc_power_on"),
    path("pc/power_off/", pc.pc_power_off, name="pc_power_off"),

    # ────────────────────────────────
    # 선풍기 제어
    # ────────────────────────────────
    path("electric_fan/power_cycle/", electric_fan.electricfan_power_cycle, name="electricfan_power_cycle"),
    path("electric_fan/stop/", electric_fan.electricfan_stop, name="electricfan_stop"),
    path("electric_fan/fan_way_toggle/", electric_fan.electricfan_fan_way_toggle, name="electricfan_fan_way_toggle"),
    path("electric_fan/timer_add_30m/", electric_fan.electricfan_timer_add_30m, name="electricfan_timer_add_30m"),

    # ────────────────────────────────
    # 시퀀스 제어
    # ────────────────────────────────
    path("sequence/create/", sequence.sequence_create, name="sequence_create",),
    path("sequence/<int:sequence_id>/run/", sequence.sequence_run, name="sequence_run",),
    path("sequence/<int:sequence_id>/delete/", sequence.sequence_delete, name="sequence_delete",),
    path("sequence/<int:sequence_id>/step/create/", sequence.sequence_step_create, name="sequence_step_create"),
    path("sequence/<int:sequence_id>/edit/", sequence.sequence_edit, name="sequence_edit"),

]

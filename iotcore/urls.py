from django.urls import path
from iotcore.api.views import base, sequence, detail, schedule
from iotcore.api.views import aircon, pc, electric_fan, ai, main_led, speaker

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
    path("schedule/", schedule.schedule_list, name="schedule_list"),
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
    # 전등 제어
    # ────────────────────────────────
    path("main_led/power_on/", main_led.main_led_power_on, name="main_led_power_on"),
    path("main_led/power_off/", main_led.main_led_power_off, name="main_led_power_off"),

    # ────────────────────────────────
    # 시퀀스 제어
    # ────────────────────────────────
    path("sequence/create/", sequence.sequence_create, name="sequence_create",),
    path("sequence/<int:sequence_id>/run/", sequence.sequence_run, name="sequence_run",),
    path("sequence/<int:sequence_id>/delete/", sequence.sequence_delete, name="sequence_delete",),
    path("sequence/<int:sequence_id>/step/create/", sequence.sequence_step_create, name="sequence_step_create"),
    path("sequence/<int:sequence_id>/update/", sequence.sequence_update, name="sequence_update"),
    path("sequence/<int:sequence_id>/edit/", sequence.sequence_edit, name="sequence_edit"),
    path("sequence/step/delete/", sequence.sequence_step_delete, name="sequence_step_delete"),

    # ────────────────────────────────
    # 스케줄 제어
    # ────────────────────────────────
    path("schedule/create/", schedule.schedule_create, name="schedule_create"),
    path("schedule/<int:schedule_id>/update/", schedule.schedule_update, name="schedule_update"),
    path("schedule/<int:schedule_id>/toggle/", schedule.schedule_toggle, name="schedule_toggle"),
    path("schedule/<int:schedule_id>/delete/", schedule.schedule_delete, name="schedule_delete"),

    # ────────────────────────────────
    # 스피커 제어
    # ────────────────────────────────
    path("speaker/<int:device_id>/playlists/<str:playlist_id>/play/", speaker.speaker_play_playlist, name="speaker_play_playlist"),
    path("speaker/<int:device_id>/music/<str:music_id>/play/", speaker.speaker_play_music, name="speaker_play_music"),
    path("speaker/<int:device_id>/previous/", speaker.speaker_play_previous, name="speaker_play_previous"),
    path("speaker/<int:device_id>/resume/", speaker.speaker_resume, name="speaker_resume"),
    path("speaker/<int:device_id>/pause/", speaker.speaker_pause, name="speaker_pause"),
    path("speaker/<int:device_id>/next/", speaker.speaker_play_next, name="speaker_play_next"),
    path("speaker/<int:device_id>/volume/", speaker.speaker_adjust_music_volume, name="speaker_adjust_music_volume"),
    path("speaker/<int:device_id>/shuffle/activate/", speaker.speaker_shuffle_activate, name="speaker_shuffle_activate"),
    path("speaker/<int:device_id>/shuffle/deactivate/", speaker.speaker_shuffle_deactivate, name="speaker_shuffle_deactivate"),
    path("speaker/<int:device_id>/repeat/<str:repeat_mode>/", speaker.speaker_set_repeat, name="speaker_set_repeat"),
]

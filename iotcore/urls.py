from django.urls import path
from django.views.generic import RedirectView

from iotcore.api.views import (
    ai,
    aircon,
    automation,
    base,
    detail,
    electric_fan,
    main_led,
    media_server,
    pc,
    projector,
    schedule,
    sequence,
    speaker,
)

app_name = "iotcore"

urlpatterns = [
    # ─────────────────────────────────────────────────────────────
    # Canonical IoTCore pages
    # ─────────────────────────────────────────────────────────────
    path(
        "",
        RedirectView.as_view(
            pattern_name="iotcore:dashboard",
            permanent=False,
        ),
    ),
    path("login/", base.login_view, name="login_view"),
    path("logout/", base.logout_view, name="logout_view"),
    path("dashboard/", base.dashboard, name="dashboard"),
    path(
        "dashboard/node-metrics/",
        base.dashboard_node_metrics,
        name="dashboard_node_metrics",
    ),
    path("devices/", detail.device_control, name="device_control"),
    path(
        "devices/projector/<int:device_id>/",
        projector.projector_control,
        name="projector_control",
    ),
    path(
        "devices/media-server/<int:device_id>/",
        media_server.media_server_control,
        name="media_server_control",
    ),
    path(
        "devices/media-server/<int:device_id>/videos/",
        media_server.media_server_videos,
        name="media_server_videos",
    ),
    path(
        "devices/media-server/<int:device_id>/play/",
        media_server.media_server_play_video,
        name="media_server_play_video",
    ),
    path(
        "devices/media-server/<int:device_id>/stop/",
        media_server.media_server_stop,
        name="media_server_stop",
    ),
    path(
        "devices/media-server/<int:device_id>/moods/play/",
        media_server.media_server_play_mood,
        name="media_server_play_mood",
    ),
    # Reverse-compatibility alias. Generated UI links use device_control.
    path("devices/", detail.device_control, name="detail_list"),
    path("sequences/", sequence.sequence_list, name="sequence_list"),
    path("automations/", automation.automation_list, name="automation_list"),
    # Reverse-compatibility alias. Generated UI links use automation_list.
    path("automations/", schedule.schedule_list, name="schedule_list"),
    path(
        "execution-history/",
        base.execution_history,
        name="execution_history",
    ),
    path("settings/", base.settings_page, name="settings"),

    # ─────────────────────────────────────────────────────────────
    # Sequence pages and actions
    # ─────────────────────────────────────────────────────────────
    path(
        "sequences/groups/",
        sequence.sequence_group_manage,
        name="sequence_group_manage",
    ),
    path(
        "sequences/create/",
        sequence.sequence_create,
        name="sequence_create",
    ),
    path(
        "sequences/<int:sequence_id>/favorite/",
        sequence.sequence_favorite_toggle,
        name="sequence_favorite_toggle",
    ),
    path(
        "sequences/<int:sequence_id>/",
        sequence.sequence_edit,
        name="sequence_edit",
    ),
    path(
        "sequences/<int:sequence_id>/settings/",
        sequence.sequence_update,
        name="sequence_update",
    ),
    path(
        "sequences/<int:sequence_id>/run/",
        sequence.sequence_run,
        name="sequence_run",
    ),
    path(
        "sequences/<int:sequence_id>/delete/",
        sequence.sequence_delete,
        name="sequence_delete",
    ),
    path(
        "sequences/<int:sequence_id>/steps/create/",
        sequence.sequence_step_create,
        name="sequence_step_create",
    ),
    path(
        "sequences/steps/delete/",
        sequence.sequence_step_delete,
        name="sequence_step_delete",
    ),

    # ─────────────────────────────────────────────────────────────
    # Automation pages and actions
    # ─────────────────────────────────────────────────────────────
    path(
        "automations/groups/",
        automation.automation_group_manage,
        name="automation_group_manage",
    ),
    path(
        "automations/create/",
        automation.automation_create,
        name="automation_create",
    ),
    path(
        "automations/<int:automation_id>/favorite/",
        automation.automation_favorite_toggle,
        name="automation_favorite_toggle",
    ),
    path(
        "automations/create/",
        schedule.schedule_create,
        name="schedule_create",
    ),
    path(
        "automations/<int:automation_id>/edit/",
        automation.automation_update,
        name="automation_update",
    ),
    path(
        "automations/<int:schedule_id>/edit/",
        schedule.schedule_update,
        name="schedule_update",
    ),
    path(
        "automations/<int:automation_id>/toggle/",
        automation.automation_toggle,
        name="automation_toggle",
    ),
    path(
        "automations/<int:schedule_id>/toggle/",
        schedule.schedule_toggle,
        name="schedule_toggle",
    ),
    path(
        "automations/<int:automation_id>/delete/",
        automation.automation_delete,
        name="automation_delete",
    ),
    path(
        "automations/<int:schedule_id>/delete/",
        schedule.schedule_delete,
        name="schedule_delete",
    ),

    # ─────────────────────────────────────────────────────────────
    # Legacy page redirects
    # ─────────────────────────────────────────────────────────────
    path(
        "detail/",
        RedirectView.as_view(
            pattern_name="iotcore:device_control",
            permanent=False,
        ),
    ),
    path(
        "sequence/",
        RedirectView.as_view(
            pattern_name="iotcore:sequence_list",
            permanent=False,
        ),
    ),
    path(
        "schedule/",
        RedirectView.as_view(
            pattern_name="iotcore:automation_list",
            permanent=False,
        ),
    ),

    # Legacy action paths are retained so old forms/bookmarks do not break.
    path("sequence/create/", sequence.sequence_create),
    path("sequence/<int:sequence_id>/run/", sequence.sequence_run),
    path("sequence/<int:sequence_id>/delete/", sequence.sequence_delete),
    path(
        "sequence/<int:sequence_id>/step/create/",
        sequence.sequence_step_create,
    ),
    path("sequence/<int:sequence_id>/update/", sequence.sequence_update),
    path("sequence/<int:sequence_id>/edit/", sequence.sequence_edit),
    path("sequence/step/delete/", sequence.sequence_step_delete),
    path("schedule/create/", schedule.schedule_create),
    path(
        "schedule/<int:schedule_id>/update/",
        schedule.schedule_update,
    ),
    path(
        "schedule/<int:schedule_id>/toggle/",
        schedule.schedule_toggle,
    ),
    path(
        "schedule/<int:schedule_id>/delete/",
        schedule.schedule_delete,
    ),

    # ─────────────────────────────────────────────────────────────
    # AI control endpoint
    # ─────────────────────────────────────────────────────────────
    path("ai_control/", ai.ai_control, name="ai_control"),

    # ─────────────────────────────────────────────────────────────
    # Device control endpoints
    # ─────────────────────────────────────────────────────────────
    path(
        "projector/<int:device_id>/control/",
        projector.projector_action,
        name="projector_action",
    ),

    path("aircon/power_on/", aircon.aircon_power_on, name="aircon_power_on"),
    path("aircon/power_off/", aircon.aircon_power_off, name="aircon_power_off"),
    path("aircon/set_temp/", aircon.aircon_set_temp, name="aircon_set_temp"),
    path("aircon/mode_auto/", aircon.aircon_mode_auto, name="aircon_mode_auto"),
    path("aircon/mode_cool/", aircon.aircon_mode_cool, name="aircon_mode_cool"),
    path(
        "aircon/mode_dehumidification/",
        aircon.aircon_dehumidification_mode,
        name="aircon_mode_dehumidification",
    ),
    path("aircon/mode_fan/", aircon.aircon_mode_fan, name="aircon_mode_fan"),

    path("pc/power_on/", pc.pc_power_on, name="pc_power_on"),
    path("pc/power_off/", pc.pc_power_off, name="pc_power_off"),

    path(
        "electric_fan/control/",
        electric_fan.electricfan_control,
        name="electricfan_control",
    ),

    path(
        "main_led/power_on/",
        main_led.main_led_power_on,
        name="main_led_power_on",
    ),
    path(
        "main_led/power_off/",
        main_led.main_led_power_off,
        name="main_led_power_off",
    ),

    # Speaker control endpoints
    path(
        "speaker/<int:device_id>/playlists/<str:playlist_id>/play/",
        speaker.speaker_play_playlist,
        name="speaker_play_playlist",
    ),
    path(
        "speaker/<int:device_id>/music/<str:music_id>/play/",
        speaker.speaker_play_music,
        name="speaker_play_music",
    ),
    path(
        "speaker/<int:device_id>/previous/",
        speaker.speaker_play_previous,
        name="speaker_play_previous",
    ),
    path(
        "speaker/<int:device_id>/resume/",
        speaker.speaker_resume,
        name="speaker_resume",
    ),
    path(
        "speaker/<int:device_id>/pause/",
        speaker.speaker_pause,
        name="speaker_pause",
    ),
    path(
        "speaker/<int:device_id>/next/",
        speaker.speaker_play_next,
        name="speaker_play_next",
    ),
    path(
        "speaker/<int:device_id>/volume/",
        speaker.speaker_adjust_music_volume,
        name="speaker_adjust_music_volume",
    ),
    path(
        "speaker/<int:device_id>/shuffle/activate/",
        speaker.speaker_shuffle_activate,
        name="speaker_shuffle_activate",
    ),
    path(
        "speaker/<int:device_id>/shuffle/deactivate/",
        speaker.speaker_shuffle_deactivate,
        name="speaker_shuffle_deactivate",
    ),
    path(
        "speaker/<int:device_id>/repeat/<str:repeat_mode>/",
        speaker.speaker_set_repeat,
        name="speaker_set_repeat",
    ),
]

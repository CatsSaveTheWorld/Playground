from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from ...models import Controller, Device
from django.conf import settings
from ...device.repositories.device_repository import DeviceRepository
from ...infrastructure.music_assistant.client import MusicAssistantClient

import pandas as pd 
import os


# CSV 파일 경로 설정
DATA_DIR = os.path.join(settings.BASE_DIR, 'iotcore', 'management', 'data')
pc_path = os.path.join(DATA_DIR, 'Computers.csv').replace('\\', '/')

# VISIBLE_PLAYLIST_NAMES = (
#     "광활",
#     "판타지",
#     "평온",
#     "슬픔",
#     "따뜻함",
#     "쓸쓸함",
#     "활기참",
#     "몽환",
#     "SF",
#     "운전",
# )


# def get_visible_playlists(playlists):
#     """음악 카드에 허용된 재생목록만 지정된 순서로 반환한다."""
#     playlist_order = {
#         name: index
#         for index, name in enumerate(VISIBLE_PLAYLIST_NAMES)
#     }
#     visible_playlists = [
#         playlist
#         for playlist in playlists
#         if playlist.get("name") in playlist_order
#     ]
#     return sorted(
#         visible_playlists,
#         key=lambda playlist: playlist_order[playlist["name"]],
#     )


@login_required(login_url="common:login")
def device_control(request):
    # Device Control에는 실제 제어 가능한 장치만 노출한다.
    # 센서는 Device에 정식 등록하되 Dashboard/예약 실행에서 상태 소스로 사용한다.
    devices = list(DeviceRepository.get_controllable())
    controllers = Controller.objects.filter(
        device__device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID]
    ).select_related("device")
    playlists = []
    playlists_error = None

    # if any(device.device_type == "speaker" for device in devices):
    #     playlists, playlists_error = MusicAssistantClient.get_playlists()
    #     playlists = get_visible_playlists(playlists)

    if any(device.device_type == "speaker" for device in devices):
        playlists, playlists_error = MusicAssistantClient.get_playlists()

    media_server_device = next(
        (device for device in devices if device.device_type == "media_server"),
        None,
    )

    pcs_df = pd.read_csv(pc_path, encoding='cp949')
    # Raspberry Pi 미디어 서버는 더 이상 PC/WOL 카드로 노출하지 않는다.
    # media_server Device를 기준으로 "내 방 제어"에 독립 카드로 표시한다.
    pcs = [
        {
            "name": row[0],
            "display_name": row[0],
            "mac": row[1],
            "broadcast_ip": row[2],
            "port": row[3],
        }
        for row in pcs_df.values
        if str(row[0]).strip() not in {"파이", "미디어 서버"}
    ]
    # ✅ PC 제어용 목록 (WOL 대상)
    # - 필요하면 DB 모델로 빼도 되지만, 우선은 여기서 관리하는 형태로 구성
    # - broadcast_ip는 보통 같은 서브넷 브로드캐스트(예: 192.168.0.255)를 쓰는 게 안정적
    # pcs = [
    #     {
    #         "id": "main_desktop",
    #         "name": "메인 데스크톱",
    #         "mac": "AA:BB:CC:DD:EE:FF",        # TODO: 실제 MAC으로 변경
    #         "broadcast_ip": "192.168.0.255",    # TODO: 네트워크에 맞게 변경
    #     }
    # ]

    context = {
        "devices": devices,
        "controllers": controllers,
        "pcs": pcs,
        "playlists": playlists,
        "playlists_error": playlists_error,
        "media_server_device": media_server_device,
    }
    return render(request, "iotcore/device_control.html", context)


# Legacy import compatibility. New code should use device_control.
detail_list = device_control

from django.shortcuts import render, get_object_or_404
from ...models import Controller, Device
from django.conf import settings
from ...device.repositories.device_repository import DeviceRepository
from ...infrastructure.music_assistant.client import MusicAssistantClient

import pandas as pd 
import os


# CSV 파일 경로 설정
DATA_DIR = os.path.join(settings.BASE_DIR, 'iotcore', 'management', 'data')
pc_path = os.path.join(DATA_DIR, 'Computers.csv').replace('\\', '/')

VISIBLE_PLAYLIST_NAMES = (
    "광활",
    "판타지",
    "평온",
    "슬픔",
    "따뜻함",
    "쓸쓸함",
    "활기참",
    "몽환",
    "SF",
    "운전",
)


def get_visible_playlists(playlists):
    """음악 카드에 허용된 재생목록만 지정된 순서로 반환한다."""
    playlist_order = {
        name: index
        for index, name in enumerate(VISIBLE_PLAYLIST_NAMES)
    }
    visible_playlists = [
        playlist
        for playlist in playlists
        if playlist.get("name") in playlist_order
    ]
    return sorted(
        visible_playlists,
        key=lambda playlist: playlist_order[playlist["name"]],
    )


def detail_list(request):
    devices = list(DeviceRepository.get_all())
    controllers = Controller.objects.all()  # 모든 컨트롤러 조회
    playlists = []
    playlists_error = None

    if any(device.device_type == "speaker" for device in devices):
        playlists, playlists_error = MusicAssistantClient.get_playlists()
        playlists = get_visible_playlists(playlists)

    pcs_df = pd.read_csv(pc_path, encoding='cp949')
    pcs = [
        {
            "name": row[0],
            "mac": row[1],
            "broadcast_ip": row[2],
            "port": row[3],
        }
        for row in pcs_df.values
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
    }
    return render(request, "iotcore/detail_list.html", context)

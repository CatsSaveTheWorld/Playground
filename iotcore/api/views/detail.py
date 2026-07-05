from django.shortcuts import render, get_object_or_404
from ...models import Controller, Device
from django.conf import settings
from ...device.repositories.device_repository import DeviceRepository

import pandas as pd 
import os


# CSV 파일 경로 설정
DATA_DIR = os.path.join(settings.BASE_DIR, 'iotcore', 'management', 'data')
pc_path = os.path.join(DATA_DIR, 'Computers.csv').replace('\\', '/')


def detail_list(request):
    devices = DeviceRepository.get_all()
    controllers = Controller.objects.all()  # 모든 컨트롤러 조회
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
    }
    return render(request, "iotcore/detail_list.html", context)


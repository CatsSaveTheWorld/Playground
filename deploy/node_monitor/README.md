# IoTCore node telemetry

This agent publishes CPU utilization/clock, RAM utilization/capacity, network throughput and storage usage once per second.
The Django MQTT listener stores each payload as one `NodeMetricSample` row.
Dashboard cards poll the latest 60 seconds every two seconds.

## MQTT topics

- Pi 5: `iotcore/nodes/pi5/telemetry`
- AI PC: `iotcore/nodes/home-ai-main/telemetry`

Telemetry is **not retained**. Online/offline is determined from the most recent DB
sample; by default a node becomes offline after 5 seconds without data.

## Broker accounts

The Pi can reuse the existing `iotcore-pi5` account. Create one account for the AI PC:

```bash
sudo mosquitto_passwd /etc/mosquitto/iotcore-agent-passwords iotcore-ai-pc
sudo cp deploy/mosquitto/iotcore-agent-acl /etc/mosquitto/iotcore-agent-acl
sudo chown mosquitto:mosquitto /etc/mosquitto/iotcore-agent-acl
sudo systemctl restart mosquitto
```

Do **not** use `-c` when adding the AI PC account; `-c` would overwrite the existing
password file.

## Pi 5

```bash
python3 -m venv ~/.local/share/iotcore-node-telemetry/venv
~/.local/share/iotcore-node-telemetry/venv/bin/pip install paho-mqtt==2.1.0 psutil
mkdir -p ~/.local/share/iotcore-node-telemetry ~/.config/iotcore-node-telemetry
cp deploy/node_monitor/node_telemetry_agent.py ~/.local/share/iotcore-node-telemetry/
cp deploy/node_monitor/node-telemetry.env.pi5.example ~/.config/iotcore-node-telemetry/agent.env
chmod 600 ~/.config/iotcore-node-telemetry/agent.env
```

The Pi example reuses the existing private MQTT password file from the YouTube Music
agent. If that path is different on the Pi, adjust `IOTCORE_MQTT_PASSWORD_FILE`. Then
install `deploy/systemd/iotcore-node-telemetry.service`.

## Windows AI PC

Use Python 3.12+ and install:

```powershell
py -m venv C:\IoTCore\node-telemetry\venv
C:\IoTCore\node-telemetry\venv\Scripts\python.exe -m pip install paho-mqtt==2.1.0 psutil
```

Copy **both** `node_telemetry_agent.py` and `ai_probe.py`, together with the AI-PC env example. A simple launcher can set
those environment variables and start the agent at logon/startup using Task Scheduler.
The `IOTCORE_NETWORK_INTERFACE` value is optional; when omitted, the agent reports
aggregate traffic across interfaces.

## DB retention

Raw one-second samples are kept for 24 hours by default. Install and enable the
cleanup timer from `deploy/systemd/iotcore-node-metric-cleanup.*` on the Django server.

## AI 추론 PC 상태 (2026-10-12)

대시보드의 AI 추론 PC 카드를 누르면 `/iotcore/dashboard/ai/` 상세 화면으로 이동합니다.
기존 CPU/RAM/저장 공간/네트워크에 GPU와 ASR/NLP 서비스 상태, 최근 20개 처리 단계 기록을 추가했습니다.
ASR/NLP의 기존 `GET /health`를 사용하므로 추론 서버 소스 변경은 필요 없습니다.

- ASR: 기본 `http://127.0.0.1:6101/health`, 모델 로딩 여부/오류/모델명/실행 장치.
- NLP: 기본 `http://127.0.0.1:6201/health`, 현재 `rule-v0` 엔진 상태.
- GPU: `nvidia-smi`로 NVIDIA GPU의 사용률/VRAM/온도를 수집. 지원하지 않거나 조회 실패 시 확인 불가로 표시.
- PC 자원은 1초, 서비스/GPU는 별도 스레드에서 약 5초마다 확인합니다. 각 조회는 타임아웃을 갖습니다.
- 성능 데이터가 5초 이상 끊기거나 서비스 확인 결과가 15초 이상 오래되면 정상 표시를 해제합니다.
- 브라우저의 갱신 요청이 실패하거나 멈춰도 화면 자체의 만료 타이머가 정상 표시를 해제합니다.
- ASR/NLP 실제 호출의 처리 중 상태, 왕복 처리 시간과 결과는 Django에 기록합니다.
  자동화 이름 직접 매칭은 외부 NLP 호출과 구분합니다. 앱의 실행 요청 등록과 실제 기기 동작 성공은 별개입니다.
- 종료 결과를 못 받은 요청은 2분 뒤 처리 중 표시를 해제합니다.
- `purge_node_metrics --hours 24`는 AI 처리 기록도 함께 정리합니다.

### 개발/운영 Django 프로젝트에 적용

DB 백업 후 실행합니다. 운영 서버에서는 기존에 사용하던 settings 옵션을 그대로 추가하세요.

```powershell
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
```

새 마이그레이션은 `0035_ai_inference_monitor`입니다. 운영 배포 시 Apache와
`iotcore-automation-listener`를 다시 시작하여 새 telemetry 형식을 반영합니다.

### Windows AI PC 에이전트 실행

기존 설치를 사용하면 에이전트 폴더에 두 Python 파일을 교체하고
`start_ai_pc_telemetry.ps1.example`의 ASR/NLP/GPU 환경 변수도 반영합니다.
처음 설치하면 위 Windows AI PC 절차로 venv를 만들고 두 파일을 복사합니다.
실행 예제의 MQTT 계정/비밀번호 파일 경로를 실제 설정에 맞추고 예제를 `.ps1`로 저장합니다.

```powershell
powershell.exe -NoProfile -File C:\IoTCore\node-telemetry\start_ai_pc_telemetry.ps1
```

환경 변수는 **에이전트 실행 PC 기준** 주소입니다. ASR/NLP가 다른 PC에 있으면 해당 주소로 변경합니다.
Pi 5 설치에서도 `ai_probe.py`를 함께 복사해야 하며, AI 관련 환경 변수를 설정하지 않으면 기존 성능 수집만 수행합니다.
상시 수집은 작업 스케줄러에서 로그인 시 실행하도록 등록할 수 있습니다.
등록할 때 `powershell.exe -NoProfile -WindowStyle Hidden -File ...`로 실행하고 실패 시 다시 시작 옵션을 설정합니다.
에이전트 실행, MQTT 계정/ACL 설정은 Django의 `collectstatic`이나 Apache 재시작만으로 수행되지 않습니다.

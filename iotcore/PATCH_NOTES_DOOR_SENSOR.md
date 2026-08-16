# Aqara P1 방문 센서 연동

## 대상

- Zigbee2MQTT Friendly Name: `livingroom_door_sensor`
- IoTCore `device_uid`: `livingroom_door_sensor`
- 역할: `sensor`
- 프로토콜: `zigbee`
- `contact=true`: 문 닫힘
- `contact=false`: 문 열림

## 적용 내용

1. 마이그레이션 `0017_door_sensor_entry_history`가 방문 센서를 `Device`에 등록/보정한다.
2. MQTT live event에서 `contact`가 실제로 변경된 경우에만 `DoorEvent`를 저장한다.
3. retained/첫 관측값은 기준 상태로만 사용하고 출입 횟수에 포함하지 않는다.
4. Dashboard의 `ROOM ENTRY / 출입 현황` 카드가 다음 값을 표시한다.
   - 현재 문 열림/닫힘 상태
   - 오늘 문이 열린 횟수
   - 마지막 개폐 이벤트 시간
5. 기존 Dashboard 3초 polling 응답에 출입 상태를 포함해 페이지 새로고침 없이 갱신한다.
6. 예약 실행에서는 등록된 센서가 기존 `DEVICE_STATE` 트리거/조건의 장치 선택지에 노출된다.

## 열림 시도

Aqara P1은 접촉 상태만 감지하므로 문이 실제로 열리지 않은 "열림 시도"는 판별할 수 없다. 해당 값은 기존 Dashboard 자리만 유지하고 `--`로 표시한다. 추후 도어락/별도 센서 이벤트가 들어오면 연결할 수 있다.

## 배포 순서

```bash
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart iotcore-automation-listener.service
sudo systemctl restart apache2
```

리스너 재시작 후 Zigbee2MQTT retained 상태가 `DeviceState`로 동기화되며 Dashboard에 현재 문 상태가 표시된다.

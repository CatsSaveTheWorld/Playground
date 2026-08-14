# Device 역할 분리 리팩토링

## 목적

센서를 `Device` 테이블에 정식 등록하면서도 `Device Control`에는 제어 가능한 장치만 노출한다. 센서/복합 장치는 Dashboard 및 예약 실행의 상태 트리거/조건에서 사용할 수 있다.

## Device.device_role

- `control`: 제어 전용 장치
- `sensor`: 상태 수집 전용 센서
- `hybrid`: 제어와 상태 수집을 모두 지원하는 장치

기존 장치는 마이그레이션 시 `control`을 유지한다. 마이그레이션 전에 등록된 행 중 `device_type`에 `sensor`가 포함된 장치와 현재 Aqara T1(`leedowon_room_temp_humidity`)은 자동으로 `sensor`로 보정한다.

## UI / 폼 노출 규칙

- Device Control: `control`, `hybrid`만 표시
- 시퀀스 단계 기기: `control`, `hybrid`만 선택 가능
- 예약 실행의 실행 동작 기기: `control`, `hybrid`만 선택 가능
- 예약 실행의 기기 상태 변화 트리거: 모든 Device 선택 가능
- 예약 실행의 기기 상태 조건: 모든 Device 선택 가능
- Dashboard: 등록된 센서를 기존 `DeviceState`와 연결해 사용

## 상태 저장

`Device`에는 장치의 정적 메타데이터를 저장하고 센서 값은 기존처럼 `DeviceState`에 저장한다. Zigbee2MQTT 장치는 `device_uid`가 Friendly Name과 일치해야 canonical state 동기화가 정상적으로 이루어진다.

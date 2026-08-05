# IoTCore 자동화 리팩토링 보완 사항

## 적용한 변경

1. `test_scheduler.py`를 구형 `Schedule` 모델 기반 테스트에서 `Automation`, `AutomationTrigger`, `AutomationCondition` 기반 테스트로 교체했습니다.
2. MQTT 이벤트 중복 실행을 막기 위해 `(automation, source_event_id)` 고유 제약을 추가했습니다.
3. 같은 MQTT 이벤트에서 같은 자동화의 복수 트리거가 동시에 일치해도 실행 요청은 한 번만 생성되도록 변경했습니다.
4. MQTT retained 메시지는 자동화를 실행하지 않고 `DeviceState`만 동기화하도록 리스너를 변경했습니다.
5. 시퀀스 워커가 여러 개 실행될 때 동일한 `SequenceRun`을 잡지 않도록 `select_for_update(skip_locked=True)`를 적용했습니다.
6. 시간 디스패처와 시퀀스 워커에서 한 건의 예외가 장기 실행 프로세스 전체를 종료시키지 않도록 예외 격리 및 DB 연결 정리를 추가했습니다.
7. `0007_unique_automation_source_event.py` 마이그레이션을 추가했습니다. 기존 중복 이벤트 실행 기록이 있으면 오래된 한 건만 남기고 중복 건을 제거한 뒤 제약을 생성합니다.

## 서버에서 적용 순서

```bash
cd <django-project-root>
source venv/bin/activate
python manage.py makemigrations --check
python manage.py migrate
python manage.py test iotcore.test_scheduler
python manage.py test
```

## 별도 프로세스 확인

다음 세 명령은 Apache 프로세스와 별도로 실행되어야 합니다.

```bash
python manage.py run_scheduler
python manage.py run_automation_listener
python manage.py run_sequence_worker
```

운영 환경에서는 각각 systemd 서비스로 등록하는 것을 권장합니다.

## 추가 확인 필요

- 이 압축본에는 `templates/`와 Django 프로젝트의 `settings.py`, `manage.py`가 포함되지 않았습니다.
- 따라서 템플릿 렌더링과 실제 MySQL 마이그레이션은 서버 프로젝트에서 검증해야 합니다.
- `MQTT_HOST`, `MQTT_PORT`, 필요 시 `MQTT_USERNAME`, `MQTT_PASSWORD` 설정을 확인하십시오.

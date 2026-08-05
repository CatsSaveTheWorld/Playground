# IoTCore 자동화 실행 대상 확장

## 변경 내용

- `Automation.sequence` 직접 참조를 제거했습니다.
- `AutomationAction`을 추가하여 자동화 하나에 다음 동작을 순서대로 등록할 수 있습니다.
  - 개별 기기 동작
  - 기존 시퀀스 실행
- `AutomationRun`, `AutomationActionRun`을 추가하여 자동화 실행과 각 동작 결과를 기록합니다.
- 기존 시간/MQTT 트리거는 이제 `AutomationRun`을 큐에 등록합니다.
- 기존 `run_sequence_worker`가 자동화 실행 큐와 수동 시퀀스 실행 큐를 모두 처리합니다.
- 자동화 작성 화면에 `실행 동작` formset을 추가했습니다.
- 기기를 선택하면 `DeviceActionRegistry`에 맞춰 동작 목록이 변경됩니다.
- 기존 자동화의 `sequence` 값은 마이그레이션 시 `AutomationAction(action_type="sequence")`로 변환됩니다.

## 적용 순서

```bash
# 기존 앱과 템플릿 백업
cp -a iotcore iotcore_backup_before_automation_actions
cp -a templates templates_backup_before_automation_actions

# 수정 파일 반영 후
source venv/bin/activate
python manage.py migrate
python manage.py test iotcore.test_scheduler
python manage.py test
```

서비스를 사용하는 경우 재시작합니다.

```bash
sudo systemctl restart apache2
sudo systemctl restart iotcore-scheduler
sudo systemctl restart iotcore-sequence-worker
sudo systemctl restart iotcore-automation-listener
```

## 주의

- 이 작업 환경에는 Django가 설치되어 있지 않고 프로젝트의 `manage.py/settings.py`가 포함되지 않아 실제 마이그레이션과 Django 테스트는 실행하지 못했습니다.
- 모든 Python 파일에 대한 문법 컴파일 검사는 통과했습니다.
- 실제 서비스 이름은 서버 설정에 맞게 바꾸십시오.

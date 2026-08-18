# 예약 실행 Trigger Set v4 리팩터링

## 확정 구조

하나의 예약 실행은 여러 개의 **트리거 세트**를 가질 수 있습니다.

각 트리거 세트는 다음을 소유합니다.

- 조건 1개 이상
- 조건 결합 방식 1개: `AND` 또는 `OR`
- 실행 동작 정확히 1개

별도의 `감시 기기`/`실행 계기` 입력은 새 UI에서 제거했습니다. 기기 상태 조건은
`기기 + 상태 필드 + 비교 기준 + 비교 값` 자체가 감시 대상과 판정식을 함께 정의합니다.

## 실행 의미

- `AND`: 세트의 모든 조건이 참일 때 세트 결과가 참입니다.
- `OR`: 세트의 조건 중 하나 이상이 참일 때 세트 결과가 참입니다.
- 세트 동작은 전체 결과가 `FALSE -> TRUE`로 전환되는 순간 한 번만 큐에 등록됩니다.
- 결과가 계속 TRUE인 동안에는 반복 실행하지 않습니다.
- 다시 FALSE가 된 뒤 TRUE가 되면 다시 실행할 수 있습니다.
- 서로 다른 트리거 세트는 독립적으로 평가/실행됩니다.

## 조건이 실행 시점을 만드는 방식

- `기기 상태`: 지정한 **그 기기의 그 상태 필드**가 바뀔 때 해당 세트를 재평가합니다.
  - 예: `Aqara T1.temperature < 24`는 `humidity` 변화 때문에 깨어나지 않습니다.
- `MQTT 이벤트`: 지정한 토픽/필터와 일치하는 메시지가 들어올 때 평가합니다.
- `예약 시간`: 스케줄러가 해당 시각에 세트를 평가합니다.
- `시간대`: 보조 조건입니다. 다른 상태/이벤트/예약 시간 조건이 세트를 깨울 때 함께 평가됩니다.

현재 구현은 한 트리거 세트에 `예약 시간` 조건을 최대 1개 허용합니다.

## 데이터 모델

기존 `AutomationTrigger` 행을 현재의 Trigger Set 컨테이너로 재사용합니다.

- `AutomationTrigger.condition_operator`: `and` / `or`
- `AutomationTrigger.last_result`: FALSE -> TRUE edge 판정용 저장 상태
- `AutomationCondition.trigger`: 조건이 소속된 Trigger Set
- `AutomationAction.trigger`: Trigger Set과 1:1

기존 `trigger_type/config` 및 action-scoped 조건 필드는 마이그레이션/구버전 데이터 호환을 위해
당분간 모델에 남겨 두되 새 UI에서는 사용하지 않습니다.

## 마이그레이션

새 마이그레이션:

```bash
python manage.py migrate
```

적용 대상은 `0020_trigger_condition_sets.py`입니다.

v3의 `감시 기기 -> 같은 기기의 상세 조건` 조합은 가능한 경우 상세 조건 하나로 합쳐집니다.
예를 들어 `Aqara T1 감시 + Aqara T1.temperature < 24`는 새 구조에서
`Aqara T1.temperature < 24` 한 조건으로 변환됩니다.

## 범위 제외

이번 변경은 예약 실행의 조건/트리거 세트 구조만 수정합니다.
IR 에어컨 등의 추정 상태(shadow/estimated state) 누적 모델은 변경하지 않습니다.

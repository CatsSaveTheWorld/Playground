"""Graph representation, validation and compatibility helpers for Automations.

The project deliberately keeps the legacy Step/Trigger/Action tables during the
first graph rollout.  This module is the single bridge between those schemas so
old automations can be migrated without changing their behaviour.
"""

from collections import defaultdict, deque
import json
import math
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

from django.db import transaction
from django.utils.dateparse import parse_datetime, parse_time

from .models import (
    Action,
    Automation,
    AutomationActionItem,
    AutomationConditionItem,
    AutomationEdge,
    AutomationNode,
    Step,
    Trigger,
    Device,
)


GRAPH_VERSION = 1


class GraphValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ConditionCapability:
    can_trigger: bool = True
    can_evaluate: bool = True


class ConditionCapabilityRegistry:
    """Capabilities are type metadata, not per-row user settings.

    Keeping this registry separate prevents Trigger/Condition options from
    drifting apart in the UI.  All current condition types support both roles;
    future run-local predicates can set ``can_trigger=False`` without creating
    a second user-facing condition catalogue.
    """

    _CAPABILITIES = {
        Trigger.Type.SCHEDULE: ConditionCapability(True, True),
        Trigger.Type.DEVICE_STATE: ConditionCapability(True, True),
        Trigger.Type.MQTT_EVENT: ConditionCapability(True, True),
        Trigger.Type.WEATHER: ConditionCapability(True, True),
    }

    @classmethod
    def get(cls, condition_type):
        return cls._CAPABILITIES.get(
            condition_type,
            ConditionCapability(False, False),
        )


class AutomationGraph:
    @staticmethod
    def _node_dict(node):
        data = {
            "key": str(node.key),
            "type": node.node_type,
            "title": node.title or "",
            "x": node.position_x,
            "y": node.position_y,
            "enabled": bool(node.enabled),
            "delay_seconds": int(node.delay_seconds or 0),
            "legacy_step_id": node.legacy_step_id,
            "conditions": [],
            "actions": [],
        }
        if node.node_type == AutomationNode.Type.CONDITION:
            data["conditions"] = [
                {
                    "id": item.pk,
                    "order": item.order,
                    "type": item.condition_type,
                    "config": item.config or {},
                    "legacy_trigger_id": item.legacy_trigger_id,
                }
                for item in node.conditions.all().order_by("order", "id")
            ]
        elif node.node_type == AutomationNode.Type.ACTION:
            data["actions"] = [
                {
                    "id": item.pk,
                    "order": item.order,
                    "action_type": item.action_type,
                    "device_id": item.device_id,
                    "function": item.function or "",
                    "parameter": item.parameter,
                    "target_automation_id": item.target_automation_id,
                    "target_automation_name": (
                        item.target_automation.name
                        if item.target_automation_id and item.target_automation
                        else ""
                    ),
                    "delay": int(item.delay or 0),
                    "delay_position": item.delay_position,
                    "legacy_action_id": item.legacy_action_id,
                }
                for item in node.actions.select_related(
                    "target_automation"
                ).order_by("order", "id")
            ]
        return data

    @classmethod
    def snapshot(cls, automation):
        nodes = list(
            automation.graph_nodes.prefetch_related(
                "conditions",
                "actions__target_automation",
            ).order_by("id")
        )
        edges = list(
            automation.graph_edges.select_related("source", "target").order_by(
                "source_id", "outcome", "order", "id"
            )
        )
        return {
            "version": GRAPH_VERSION,
            "automation_id": automation.pk,
            "automation_name": automation.name,
            "revision": int(automation.graph_revision or 0),
            "nodes": [cls._node_dict(node) for node in nodes],
            "edges": [
                {
                    "source": str(edge.source.key),
                    "target": str(edge.target.key),
                    "outcome": edge.outcome,
                    "order": edge.order,
                }
                for edge in edges
            ],
        }

    @classmethod
    def validate_automation(cls, automation):
        return cls.validate_snapshot(cls.snapshot(automation))

    @classmethod
    def validate_snapshot(cls, snapshot):
        nodes = snapshot.get("nodes") or []
        edges = snapshot.get("edges") or []
        if not nodes:
            raise GraphValidationError("그래프에 Node가 없습니다.")

        node_map = {}
        for node in nodes:
            key = str(node.get("key") or "")
            if not key or key in node_map:
                raise GraphValidationError("Node key가 없거나 중복되었습니다.")
            if node.get("type") not in AutomationNode.Type.values:
                raise GraphValidationError(f"지원하지 않는 Node 유형입니다: {node.get('type')}")
            node_map[key] = node

        starts = [n for n in nodes if n["type"] == AutomationNode.Type.START]
        if len(starts) != 1:
            raise GraphValidationError("START Node는 정확히 하나여야 합니다.")
        if not any(n["type"] == AutomationNode.Type.END for n in nodes):
            raise GraphValidationError("END Node가 하나 이상 필요합니다.")
        if not any(n["type"] == AutomationNode.Type.ACTION for n in nodes):
            raise GraphValidationError("동작 Node가 하나 이상 필요합니다.")

        outgoing = defaultdict(list)
        incoming = defaultdict(list)
        seen_orders = set()
        seen_edges = set()
        for edge in edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            outcome = edge.get("outcome")
            if source not in node_map or target not in node_map:
                raise GraphValidationError("존재하지 않는 Node를 연결한 Edge가 있습니다.")
            if source == target:
                raise GraphValidationError("Node 자신에게 연결할 수 없습니다.")
            if outcome not in AutomationEdge.Outcome.values:
                raise GraphValidationError("지원하지 않는 Edge outcome입니다.")
            try:
                order = int(edge.get("order", 0))
            except (TypeError, ValueError):
                order = 0
            if order <= 0:
                raise GraphValidationError("Edge order는 1 이상의 정수여야 합니다.")
            edge_key = (source, target, outcome)
            if edge_key in seen_edges:
                raise GraphValidationError("동일한 Node 연결이 중복되었습니다.")
            seen_edges.add(edge_key)
            order_key = (source, outcome, order)
            if order_key in seen_orders:
                raise GraphValidationError("같은 출력의 Edge 실행 순서가 중복되었습니다.")
            seen_orders.add(order_key)

            if node_map[target]["type"] == AutomationNode.Type.START:
                raise GraphValidationError("START Node에는 Edge를 연결할 수 없습니다.")

            source_type = node_map[source]["type"]
            if source_type == AutomationNode.Type.CONDITION:
                if outcome not in {AutomationEdge.Outcome.TRUE, AutomationEdge.Outcome.FALSE}:
                    raise GraphValidationError("조건 Node는 TRUE/FALSE Edge만 사용할 수 있습니다.")
            elif source_type in {
                AutomationNode.Type.START,
                AutomationNode.Type.ACTION,
                AutomationNode.Type.DELAY,
            }:
                if outcome != AutomationEdge.Outcome.ALWAYS:
                    raise GraphValidationError("START/ACTION/DELAY Node는 ALWAYS Edge만 사용할 수 있습니다.")
            elif source_type == AutomationNode.Type.END:
                raise GraphValidationError("END Node에서는 Edge가 나갈 수 없습니다.")

            outgoing[source].append((order, target, outcome))
            incoming[target].append((source, outcome))

        for key, node in node_map.items():
            node_type = node["type"]
            if node_type == AutomationNode.Type.CONDITION:
                conditions = node.get("conditions") or []
                if not conditions:
                    raise GraphValidationError("조건 Node에는 조건이 하나 이상 필요합니다.")
                schedule_count = sum(
                    1 for item in conditions
                    if item.get("type") == Trigger.Type.SCHEDULE
                )
                if schedule_count > 1:
                    raise GraphValidationError("한 조건 Node에는 날짜/시간 조건을 하나만 둘 수 있습니다.")
                incoming_sources = incoming.get(key, [])
                has_start_input = any(
                    node_map[source]["type"] == AutomationNode.Type.START
                    for source, _ in incoming_sources
                )
                has_runtime_input = any(
                    node_map[source]["type"] != AutomationNode.Type.START
                    for source, _ in incoming_sources
                )
                for item in conditions:
                    capability = ConditionCapabilityRegistry.get(item.get("type"))
                    if has_start_input and not capability.can_trigger:
                        raise GraphValidationError("시작 조건으로 사용할 수 없는 조건이 포함되어 있습니다.")
                    if has_runtime_input and not capability.can_evaluate:
                        raise GraphValidationError("분기 조건으로 사용할 수 없는 조건이 포함되어 있습니다.")
            elif node_type == AutomationNode.Type.ACTION:
                if not (node.get("actions") or []):
                    raise GraphValidationError("동작 Node에는 동작이 하나 이상 필요합니다.")
            elif node_type == AutomationNode.Type.DELAY:
                try:
                    delay = int(node.get("delay_seconds", 0))
                except (TypeError, ValueError):
                    delay = -1
                if delay < 0:
                    raise GraphValidationError("지연 시간은 0초 이상이어야 합니다.")

        # Kahn topological sort: any leftover node means a cycle exists.
        indegree = {key: 0 for key in node_map}
        for _source, targets in outgoing.items():
            for _order, target, _outcome in targets:
                indegree[target] += 1
        queue = deque(key for key, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            key = queue.popleft()
            visited += 1
            for _order, target, _outcome in outgoing.get(key, []):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(node_map):
            raise GraphValidationError("순환 경로는 허용되지 않습니다.")

        start_key = str(starts[0]["key"])
        reachable = {start_key}
        queue = deque([start_key])
        while queue:
            key = queue.popleft()
            for _order, target, _outcome in outgoing.get(key, []):
                if target not in reachable:
                    reachable.add(target)
                    queue.append(target)
        unreachable = set(node_map) - reachable
        if unreachable:
            raise GraphValidationError("START에서 도달할 수 없는 Node가 있습니다.")

        return True

    @staticmethod
    def new_editor_payload():
        start_key = str(uuid.uuid4())
        action_key = str(uuid.uuid4())
        end_key = str(uuid.uuid4())
        return {
            "version": GRAPH_VERSION,
            "revision": 0,
            "nodes": [
                {
                    "key": start_key, "type": AutomationNode.Type.START,
                    "title": "START", "x": 80, "y": 220, "enabled": True,
                    "delay_seconds": 0, "conditions": [], "actions": [],
                },
                {
                    "key": action_key, "type": AutomationNode.Type.ACTION,
                    "title": "동작", "x": 420, "y": 220, "enabled": True,
                    "delay_seconds": 0, "conditions": [], "actions": [],
                },
                {
                    "key": end_key, "type": AutomationNode.Type.END,
                    "title": "END", "x": 760, "y": 220, "enabled": True,
                    "delay_seconds": 0, "conditions": [], "actions": [],
                },
            ],
            "edges": [
                {"source": start_key, "target": action_key, "outcome": AutomationEdge.Outcome.ALWAYS, "order": 1},
                {"source": action_key, "target": end_key, "outcome": AutomationEdge.Outcome.ALWAYS, "order": 1},
            ],
        }

    @classmethod
    def editor_payload(cls, automation):
        if automation.pk and automation.graph_nodes.exists():
            return cls.snapshot(automation)
        return cls.new_editor_payload()

    @staticmethod
    def _normalize_condition_config(condition_type, config):
        config = dict(config or {})
        if condition_type == Trigger.Type.SCHEDULE:
            schedule_type = config.get("schedule_type")
            if schedule_type == Trigger.ScheduleType.ONCE:
                run_at = parse_datetime(str(config.get("run_at") or ""))
                if run_at is None:
                    raise GraphValidationError("한 번 실행 시각이 올바르지 않습니다.")
                return {"schedule_type": schedule_type, "run_at": run_at.isoformat()}
            if schedule_type == Trigger.ScheduleType.WEEKLY:
                try:
                    weekdays = sorted({int(v) for v in config.get("weekdays", [])})
                except (TypeError, ValueError):
                    weekdays = []
                if not weekdays or not set(weekdays).issubset(set(range(7))):
                    raise GraphValidationError("요일을 하나 이상 올바르게 선택하세요.")
                mode = config.get("time_mode") or Trigger.ScheduleTimeMode.AT
                if mode == Trigger.ScheduleTimeMode.WINDOW:
                    start = parse_time(str(config.get("start") or ""))
                    if start is None:
                        raise GraphValidationError("시간대 시작 시각이 올바르지 않습니다.")
                    raw_end = config.get("end")
                    end = None if raw_end in (None, "") else parse_time(str(raw_end))
                    if raw_end not in (None, "") and end is None:
                        raise GraphValidationError("시간대 종료 시각이 올바르지 않습니다.")
                    return {
                        "schedule_type": schedule_type,
                        "weekdays": weekdays,
                        "time_mode": mode,
                        "start": start.strftime("%H:%M"),
                        "end": end.strftime("%H:%M") if end else None,
                    }
                value = parse_time(str(config.get("time") or ""))
                if value is None:
                    raise GraphValidationError("반복 실행 시간이 올바르지 않습니다.")
                return {
                    "schedule_type": schedule_type,
                    "weekdays": weekdays,
                    "time_mode": Trigger.ScheduleTimeMode.AT,
                    "time": value.strftime("%H:%M"),
                }
            if schedule_type == Trigger.ScheduleType.INTERVAL:
                try:
                    every = int(config.get("every") or 0)
                except (TypeError, ValueError):
                    every = 0
                unit = config.get("unit")
                if every <= 0 or unit not in {"minutes", "hours", "days"}:
                    raise GraphValidationError("반복 간격이 올바르지 않습니다.")
                return {"schedule_type": schedule_type, "every": every, "unit": unit}
            raise GraphValidationError("반복 방식을 선택하세요.")

        if condition_type == Trigger.Type.DEVICE_STATE:
            try:
                device_id = int(config.get("device_id"))
            except (TypeError, ValueError):
                device_id = None
            device = Device.objects.filter(pk=device_id).first() if device_id else None
            if device is None:
                raise GraphValidationError("기기 상태 조건의 기기를 선택하세요.")
            key = str(config.get("key") or "").strip()
            operator = str(config.get("operator") or "").strip()
            if not key:
                raise GraphValidationError("기기 상태 키를 입력하세요.")
            if operator not in {"eq", "ne", "gt", "gte", "lt", "lte", "changed", "changed_to"}:
                raise GraphValidationError("기기 상태 비교 방식이 올바르지 않습니다.")
            return {
                "device_id": device.pk,
                "device_uid": device.device_uid,
                "device_name": device.name,
                "key": key,
                "operator": operator,
                "value": config.get("value"),
            }

        if condition_type == Trigger.Type.MQTT_EVENT:
            topic = str(config.get("topic") or "").strip()
            operator = str(config.get("operator") or "received").strip() or "received"
            if not topic:
                raise GraphValidationError("MQTT 토픽을 입력하세요.")
            if operator not in {"received", "eq", "ne", "gt", "gte", "lt", "lte", "changed", "changed_to"}:
                raise GraphValidationError("MQTT 비교 방식이 올바르지 않습니다.")
            return {
                "topic": topic,
                "field": str(config.get("field") or "value").strip() or "value",
                "operator": operator,
                "value": config.get("value"),
            }

        if condition_type == Trigger.Type.WEATHER:
            metric = config.get("metric")
            operator = config.get("operator")
            if metric not in {"temperature", "humidity", "precipitation_probability"}:
                raise GraphValidationError("날씨 항목이 올바르지 않습니다.")
            if operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                raise GraphValidationError("날씨 비교 방식이 올바르지 않습니다.")
            try:
                value = float(config.get("value"))
            except (TypeError, ValueError):
                raise GraphValidationError("날씨 비교 값을 숫자로 입력하세요.")
            if not math.isfinite(value):
                raise GraphValidationError("날씨 비교 값이 올바르지 않습니다.")
            return {"metric": metric, "operator": operator, "value": value}

        raise GraphValidationError("지원하지 않는 조건 유형입니다.")

    @staticmethod
    def _normalize_action(action):
        # Reuse the production ActionForm safety checks so the graph editor and
        # legacy editor cannot diverge on supported device commands.
        from .forms import ActionForm

        action_type = action.get("action_type") or Action.Type.DEVICE
        parameter = action.get("parameter")
        data = {
            "action_type": action_type,
            "device": action.get("device_id") or "",
            "function": action.get("function") or "",
            "parameter_json": "" if parameter is None else json.dumps(parameter, ensure_ascii=False),
            "sequence": action.get("target_automation_id") or "",
            "delay": action.get("delay") or 0,
            "delay_position": action.get("delay_position") or Action.DelayPosition.AFTER,
        }
        form = ActionForm(data=data)
        if not form.is_valid():
            errors = "; ".join(
                f"{field}: {', '.join(str(message) for message in messages)}"
                for field, messages in form.errors.items()
            )
            raise GraphValidationError(f"동작 설정이 올바르지 않습니다. {errors}")
        cleaned = form.cleaned_data
        device = cleaned.get("device")
        target = cleaned.get("target_automation")
        return {
            "action_type": cleaned.get("action_type") or Action.Type.DEVICE,
            "device_id": device.pk if device else None,
            "function": cleaned.get("function") or "",
            "parameter": cleaned.get("parameter"),
            "target_automation_id": target.pk if target else None,
            "target_automation_name": target.name if target else "",
            "delay": int(cleaned.get("delay") or 0),
            "delay_position": cleaned.get("delay_position") or Action.DelayPosition.AFTER,
        }

    @classmethod
    def normalize_editor_payload(cls, payload):
        if not isinstance(payload, dict):
            raise GraphValidationError("그래프 데이터 형식이 올바르지 않습니다.")
        raw_nodes = payload.get("nodes")
        raw_edges = payload.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise GraphValidationError("Node/Edge 목록이 필요합니다.")

        nodes = []
        node_by_key = {}
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                raise GraphValidationError("Node 데이터 형식이 올바르지 않습니다.")
            try:
                key = str(uuid.UUID(str(raw.get("key"))))
            except (TypeError, ValueError, AttributeError):
                raise GraphValidationError("Node key가 UUID 형식이 아닙니다.")
            if key in node_by_key:
                raise GraphValidationError("Node key가 중복되었습니다.")
            node_type = raw.get("type")
            if node_type not in AutomationNode.Type.values:
                raise GraphValidationError("지원하지 않는 Node 유형입니다.")
            try:
                x = int(round(float(raw.get("x", 0))))
                y = int(round(float(raw.get("y", 0))))
            except (TypeError, ValueError):
                raise GraphValidationError("Node 위치가 올바르지 않습니다.")
            try:
                delay_seconds = int(raw.get("delay_seconds") or 0)
            except (TypeError, ValueError):
                raise GraphValidationError("지연 시간이 올바르지 않습니다.")
            if delay_seconds < 0:
                raise GraphValidationError("지연 시간은 0초 이상이어야 합니다.")
            node = {
                "key": key,
                "type": node_type,
                "title": str(raw.get("title") or "")[:100],
                "x": x,
                "y": y,
                "enabled": bool(raw.get("enabled", True)),
                "delay_seconds": delay_seconds,
                "conditions": [],
                "actions": [],
            }
            if node_type == AutomationNode.Type.CONDITION:
                raw_conditions = raw.get("conditions") or []
                if not isinstance(raw_conditions, list):
                    raise GraphValidationError("조건 목록 형식이 올바르지 않습니다.")
                for order, item in enumerate(raw_conditions, start=1):
                    condition_type = item.get("type") or item.get("condition_type")
                    if condition_type not in Trigger.Type.values:
                        raise GraphValidationError("지원하지 않는 조건 유형입니다.")
                    node["conditions"].append({
                        "order": order,
                        "type": condition_type,
                        "config": cls._normalize_condition_config(
                            condition_type, item.get("config") or {}
                        ),
                    })
            elif node_type == AutomationNode.Type.ACTION:
                raw_actions = raw.get("actions") or []
                if not isinstance(raw_actions, list):
                    raise GraphValidationError("동작 목록 형식이 올바르지 않습니다.")
                for order, item in enumerate(raw_actions, start=1):
                    normalized = cls._normalize_action(item or {})
                    normalized["order"] = order
                    node["actions"].append(normalized)
            nodes.append(node)
            node_by_key[key] = node

        edge_groups = defaultdict(list)
        for index, raw in enumerate(raw_edges):
            if not isinstance(raw, dict):
                raise GraphValidationError("Edge 데이터 형식이 올바르지 않습니다.")
            source = str(raw.get("source") or "")
            target = str(raw.get("target") or "")
            outcome = raw.get("outcome") or AutomationEdge.Outcome.ALWAYS
            if source not in node_by_key or target not in node_by_key:
                raise GraphValidationError("존재하지 않는 Node를 연결한 Edge가 있습니다.")
            edge_groups[(source, outcome)].append((index, target))

        edges = []
        # Top-to-bottom visual position is the user-facing execution order. The
        # stable index is materialized here and is what the runtime executes.
        for (source, outcome), values in edge_groups.items():
            values.sort(
                key=lambda pair: (
                    node_by_key[pair[1]]["y"],
                    node_by_key[pair[1]]["x"],
                    pair[0],
                )
            )
            for order, (_index, target) in enumerate(values, start=1):
                edges.append({
                    "source": source,
                    "target": target,
                    "outcome": outcome,
                    "order": order,
                })

        normalized = {
            "version": GRAPH_VERSION,
            "revision": int(payload.get("revision") or 0),
            "nodes": nodes,
            "edges": edges,
        }
        cls.validate_snapshot(normalized)
        return normalized

    @classmethod
    @transaction.atomic
    def replace_from_payload(cls, automation, payload):
        normalized = cls.normalize_editor_payload(payload)
        automation = Automation.objects.select_for_update().get(pk=automation.pk)
        submitted_revision = int(normalized.get("revision") or 0)
        if (
            automation.graph_version == GRAPH_VERSION
            and automation.graph_nodes.exists()
            and submitted_revision != int(automation.graph_revision or 0)
        ):
            raise GraphValidationError(
                "다른 화면에서 이 자동화가 먼저 수정되었습니다. "
                "최신 그래프를 다시 연 뒤 변경 내용을 적용하세요."
            )
        automation.graph_nodes.all().delete()

        node_models = {}
        for data in normalized["nodes"]:
            node = AutomationNode.objects.create(
                automation=automation,
                key=uuid.UUID(data["key"]),
                node_type=data["type"],
                title=data.get("title") or "",
                position_x=data["x"],
                position_y=data["y"],
                enabled=data.get("enabled", True),
                delay_seconds=data.get("delay_seconds", 0),
            )
            node_models[data["key"]] = node
            for item in data.get("conditions", []):
                AutomationConditionItem.objects.create(
                    node=node,
                    order=item["order"],
                    condition_type=item["type"],
                    config=item.get("config") or {},
                )
            for item in data.get("actions", []):
                AutomationActionItem.objects.create(
                    node=node,
                    order=item["order"],
                    action_type=item["action_type"],
                    device_id=item.get("device_id"),
                    function=item.get("function") or "",
                    parameter=item.get("parameter"),
                    target_automation_id=item.get("target_automation_id"),
                    delay=item.get("delay", 0),
                    delay_position=item.get("delay_position") or Action.DelayPosition.AFTER,
                )
        for edge in normalized["edges"]:
            AutomationEdge.objects.create(
                automation=automation,
                source=node_models[edge["source"]],
                target=node_models[edge["target"]],
                outcome=edge["outcome"],
                order=edge["order"],
            )

        cls.validate_automation(automation)
        automation.graph_version = GRAPH_VERSION
        automation.graph_revision += 1
        automation.save(update_fields=["graph_version", "graph_revision", "updated_at"])
        return automation

    @classmethod
    def condition_as_trigger(cls, item):
        """Return a Trigger-like object accepted by AutomationService."""
        return SimpleNamespace(
            pk=item.get("legacy_trigger_id") or item.get("id"),
            trigger_type=item.get("type"),
            config=item.get("config") or {},
        )

    @classmethod
    def legacy_trigger_entry_key(cls, automation, trigger_id):
        if not trigger_id:
            return None
        return (
            automation.graph_nodes.filter(
                node_type=AutomationNode.Type.CONDITION,
                conditions__legacy_trigger_id=trigger_id,
            )
            .order_by("position_y", "id")
            .values_list("key", flat=True)
            .first()
        )

    @classmethod
    def legacy_step_entry_key(cls, automation, step_id):
        if not step_id:
            return None
        value = (
            automation.graph_nodes.filter(
                node_type=AutomationNode.Type.CONDITION,
                legacy_step_id=step_id,
            )
            .order_by("position_y", "id")
            .values_list("key", flat=True)
            .first()
        )
        if value:
            return value
        # Unconditional legacy Step is mapped directly to its ACTION node.
        return (
            automation.graph_nodes.filter(
                node_type=AutomationNode.Type.ACTION,
                legacy_step_id=step_id,
            )
            .order_by("position_y", "id")
            .values_list("key", flat=True)
            .first()
        )

    @classmethod
    @transaction.atomic
    def sync_from_legacy(cls, automation, *, activate=True):
        """Rebuild graph rows from the legacy Step schema without data loss.

        This is intentionally deterministic so editing through the old form
        during the transition cannot leave stale graph rows behind.
        """
        automation = Automation.objects.select_for_update().get(pk=automation.pk)
        automation.graph_nodes.all().delete()

        steps = list(
            automation.steps.prefetch_related(
                "triggers",
                "actions__device",
                "actions__target_automation",
            ).order_by("order", "id")
        )
        if not steps:
            if activate:
                automation.graph_version = 0
                automation.graph_revision += 1
                automation.save(update_fields=["graph_version", "graph_revision", "updated_at"])
            return False
        if any(not list(step.actions.all()) for step in steps):
            if activate:
                automation.graph_version = 0
                automation.graph_revision += 1
                automation.save(update_fields=["graph_version", "graph_revision", "updated_at"])
            return False

        start = AutomationNode.objects.create(
            automation=automation,
            node_type=AutomationNode.Type.START,
            title="START",
            position_x=40,
            position_y=80,
        )
        end = AutomationNode.objects.create(
            automation=automation,
            node_type=AutomationNode.Type.END,
            title="END",
            position_x=960,
            position_y=80 + max(len(steps) - 1, 0) * 180 // 2,
        )

        start_edge_order = 0
        for branch_index, step in enumerate(steps):
            y = 80 + branch_index * 180
            triggers = list(step.triggers.order_by("order", "id"))
            actions = list(step.actions.order_by("order", "id"))
            action_node = AutomationNode.objects.create(
                automation=automation,
                node_type=AutomationNode.Type.ACTION,
                title=f"Step {step.order} 동작",
                position_x=650,
                position_y=y,
                enabled=step.enabled,
                legacy_step=step,
            )
            for action in actions:
                AutomationActionItem.objects.create(
                    node=action_node,
                    order=action.order,
                    action_type=action.action_type,
                    device_id=action.device_id,
                    function=action.function,
                    parameter=action.parameter,
                    target_automation_id=action.target_automation_id,
                    delay=action.delay,
                    delay_position=action.delay_position,
                    legacy_action=action,
                )
            AutomationEdge.objects.create(
                automation=automation,
                source=action_node,
                target=end,
                outcome=AutomationEdge.Outcome.ALWAYS,
                order=1,
            )

            if not triggers:
                start_edge_order += 1
                AutomationEdge.objects.create(
                    automation=automation,
                    source=start,
                    target=action_node,
                    outcome=AutomationEdge.Outcome.ALWAYS,
                    order=start_edge_order,
                )
                continue

            if step.trigger_operator == Step.TriggerOperator.OR and len(triggers) > 1:
                for offset, trigger in enumerate(triggers):
                    condition_node = AutomationNode.objects.create(
                        automation=automation,
                        node_type=AutomationNode.Type.CONDITION,
                        title=f"Step {step.order} 조건 {offset + 1}",
                        position_x=330,
                        position_y=y + offset * 70,
                        enabled=step.enabled,
                        legacy_step=step,
                        last_result=step.last_result,
                        next_run_at=step.next_run_at,
                        last_triggered_at=step.last_triggered_at,
                    )
                    AutomationConditionItem.objects.create(
                        node=condition_node,
                        order=1,
                        condition_type=trigger.trigger_type,
                        config=trigger.config or {},
                        legacy_trigger=trigger,
                    )
                    start_edge_order += 1
                    AutomationEdge.objects.create(
                        automation=automation,
                        source=start,
                        target=condition_node,
                        outcome=AutomationEdge.Outcome.ALWAYS,
                        order=start_edge_order,
                    )
                    AutomationEdge.objects.create(
                        automation=automation,
                        source=condition_node,
                        target=action_node,
                        outcome=AutomationEdge.Outcome.TRUE,
                        order=1,
                    )
                    AutomationEdge.objects.create(
                        automation=automation,
                        source=condition_node,
                        target=end,
                        outcome=AutomationEdge.Outcome.FALSE,
                        order=1,
                    )
            else:
                condition_node = AutomationNode.objects.create(
                    automation=automation,
                    node_type=AutomationNode.Type.CONDITION,
                    title=f"Step {step.order} 조건",
                    position_x=330,
                    position_y=y,
                    enabled=step.enabled,
                    legacy_step=step,
                    last_result=step.last_result,
                    next_run_at=step.next_run_at,
                    last_triggered_at=step.last_triggered_at,
                )
                for trigger in triggers:
                    AutomationConditionItem.objects.create(
                        node=condition_node,
                        order=trigger.order,
                        condition_type=trigger.trigger_type,
                        config=trigger.config or {},
                        legacy_trigger=trigger,
                    )
                start_edge_order += 1
                AutomationEdge.objects.create(
                    automation=automation,
                    source=start,
                    target=condition_node,
                    outcome=AutomationEdge.Outcome.ALWAYS,
                    order=start_edge_order,
                )
                AutomationEdge.objects.create(
                    automation=automation,
                    source=condition_node,
                    target=action_node,
                    outcome=AutomationEdge.Outcome.TRUE,
                    order=1,
                )
                AutomationEdge.objects.create(
                    automation=automation,
                    source=condition_node,
                    target=end,
                    outcome=AutomationEdge.Outcome.FALSE,
                    order=1,
                )

        cls.validate_automation(automation)
        automation.graph_revision += 1
        if activate:
            automation.graph_version = GRAPH_VERSION
        automation.save(update_fields=["graph_version", "graph_revision", "updated_at"])
        return True

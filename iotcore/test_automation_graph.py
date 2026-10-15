import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from .automation_graph import AutomationGraph, GRAPH_VERSION, GraphValidationError
from .models import (
    Action,
    ActionRun,
    Automation,
    AutomationActionItem,
    AutomationConditionItem,
    AutomationEdge,
    AutomationNode,
    AutomationNodeRun,
    AutomationRun,
    Device,
    DeviceState,
    Step,
    Trigger,
)
from .scheduler.executor import AutomationExecutor
from .scheduler.service import AutomationService


class AutomationGraphTestMixin:
    def setUp(self):
        super().setUp()
        self.light = Device.objects.create(
            device_type="light",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.ZIGBEE,
            device_uid="graph-test-light",
            name="Graph Test Light",
            location="test",
        )
        self.sensor = Device.objects.create(
            device_type="sensor",
            device_role=Device.Role.SENSOR,
            protocol=Device.Protocol.ZIGBEE,
            device_uid="graph-test-sensor",
            name="Graph Test Sensor",
            location="test",
        )

    def make_automation(self, *, name="Graph Test", automation_type=Automation.Type.IMMEDIATE):
        return Automation.objects.create(
            name=name,
            automation_type=automation_type,
            graph_version=GRAPH_VERSION,
            graph_revision=1,
        )

    def node(self, automation, node_type, *, title="", x=0, y=0, delay=0, enabled=True):
        return AutomationNode.objects.create(
            automation=automation,
            node_type=node_type,
            title=title,
            position_x=x,
            position_y=y,
            delay_seconds=delay,
            enabled=enabled,
        )

    def edge(self, automation, source, target, outcome=AutomationEdge.Outcome.ALWAYS, order=1):
        return AutomationEdge.objects.create(
            automation=automation,
            source=source,
            target=target,
            outcome=outcome,
            order=order,
        )

    def condition(self, node, *, order=1, condition_type=Trigger.Type.DEVICE_STATE, config=None):
        if config is None:
            config = {
                "device_id": self.sensor.pk,
                "device_uid": self.sensor.device_uid,
                "device_name": self.sensor.name,
                "key": "occupied",
                "operator": "eq",
                "value": True,
            }
        return AutomationConditionItem.objects.create(
            node=node,
            order=order,
            condition_type=condition_type,
            config=config,
        )

    def action(self, node, *, order=1, function="power_on", device=None, target=None):
        if target is not None:
            return AutomationActionItem.objects.create(
                node=node,
                order=order,
                action_type=Action.Type.AUTOMATION,
                target_automation=target,
            )
        return AutomationActionItem.objects.create(
            node=node,
            order=order,
            action_type=Action.Type.DEVICE,
            device=device or self.light,
            function=function,
        )

    def set_state(self, key, value):
        DeviceState.objects.update_or_create(
            topic=AutomationService.canonical_state_topic(self.sensor),
            key=key,
            defaults={"value": value},
        )

    def simple_graph(self, automation, *, action_function="power_on"):
        start = self.node(automation, AutomationNode.Type.START, x=0, y=100)
        action = self.node(automation, AutomationNode.Type.ACTION, x=300, y=100)
        end = self.node(automation, AutomationNode.Type.END, x=600, y=100)
        self.action(action, function=action_function)
        self.edge(automation, start, action)
        self.edge(automation, action, end)
        AutomationGraph.validate_automation(automation)
        return start, action, end


class AutomationGraphValidationTests(AutomationGraphTestMixin, TestCase):
    def test_cycle_is_rejected(self):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        action = self.node(automation, AutomationNode.Type.ACTION)
        end = self.node(automation, AutomationNode.Type.END)
        self.action(action)
        self.edge(automation, start, action)
        self.edge(automation, action, end, order=1)
        self.edge(automation, action, action, order=2)

        with self.assertRaises(GraphValidationError):
            AutomationGraph.validate_automation(automation)

    def test_unreachable_node_is_rejected(self):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        action = self.node(automation, AutomationNode.Type.ACTION)
        orphan = self.node(automation, AutomationNode.Type.ACTION, y=300)
        end = self.node(automation, AutomationNode.Type.END)
        self.action(action)
        self.action(orphan, function="power_off")
        self.edge(automation, start, action)
        self.edge(automation, action, end)

        with self.assertRaisesMessage(GraphValidationError, "도달"):
            AutomationGraph.validate_automation(automation)

    def test_condition_only_allows_true_false_edges(self):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        condition = self.node(automation, AutomationNode.Type.CONDITION)
        action = self.node(automation, AutomationNode.Type.ACTION)
        end = self.node(automation, AutomationNode.Type.END)
        self.condition(condition)
        self.action(action)
        self.edge(automation, start, condition)
        self.edge(automation, condition, action, AutomationEdge.Outcome.ALWAYS)
        self.edge(automation, action, end)

        with self.assertRaisesMessage(GraphValidationError, "TRUE/FALSE"):
            AutomationGraph.validate_automation(automation)

    def test_editor_normalization_orders_edges_by_target_y(self):
        automation = self.make_automation()
        device_id = self.light.pk
        start = str(uuid.uuid4())
        upper = str(uuid.uuid4())
        lower = str(uuid.uuid4())
        end = str(uuid.uuid4())
        action = lambda key, y, fn: {
            "key": key, "type": "action", "title": fn, "x": 300, "y": y,
            "enabled": True,
            "actions": [{
                "action_type": "device", "device_id": device_id,
                "function": fn, "parameter": None,
                "target_automation_id": None, "delay": 0,
                "delay_position": "after",
            }],
        }
        payload = {
            "version": 1,
            "revision": automation.graph_revision,
            "nodes": [
                {"key": start, "type": "start", "title": "START", "x": 0, "y": 0, "enabled": True},
                action(lower, 500, "power_off"),
                action(upper, 100, "power_on"),
                {"key": end, "type": "end", "title": "END", "x": 700, "y": 250, "enabled": True},
            ],
            # Intentionally submit the lower target first. The server must not
            # trust request ordering for user-facing flow order.
            "edges": [
                {"source": start, "target": lower, "outcome": "always"},
                {"source": start, "target": upper, "outcome": "always"},
                {"source": upper, "target": end, "outcome": "always"},
                {"source": lower, "target": end, "outcome": "always"},
            ],
        }

        normalized = AutomationGraph.normalize_editor_payload(payload)
        start_edges = sorted(
            [edge for edge in normalized["edges"] if edge["source"] == start],
            key=lambda edge: edge["order"],
        )
        self.assertEqual([edge["target"] for edge in start_edges], [upper, lower])
        self.assertEqual([edge["order"] for edge in start_edges], [1, 2])

    def test_stale_graph_revision_is_rejected(self):
        automation = self.make_automation()
        self.simple_graph(automation)
        current = AutomationGraph.snapshot(automation)

        AutomationGraph.replace_from_payload(automation, current)
        automation.refresh_from_db()
        self.assertEqual(automation.graph_revision, 2)

        with self.assertRaisesMessage(GraphValidationError, "먼저 수정"):
            AutomationGraph.replace_from_payload(automation, current)


class AutomationGraphLayoutOrderTests(AutomationGraphTestMixin, TestCase):
    def ordered_graph(self):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START, x=0, y=0)
        first = self.node(automation, AutomationNode.Type.ACTION, x=300, y=500)
        second = self.node(automation, AutomationNode.Type.ACTION, x=300, y=0)
        end = self.node(automation, AutomationNode.Type.END, x=600, y=200)
        self.action(first, function="power_on")
        self.action(second, function="power_off")
        self.edge(automation, start, first, order=1)
        self.edge(automation, start, second, order=2)
        self.edge(automation, first, end)
        self.edge(automation, second, end)
        return automation, start, first, second

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    @patch.object(AutomationExecutor, "DEFAULT_ACTION_DELAY", 0)
    def test_layout_only_save_preserves_snapshot_and_device_execution_order(self, execute_step):
        automation, start, first, second = self.ordered_graph()
        payload = AutomationGraph.snapshot(automation)
        # Deliberately reverse the visible order and the transport array order.
        for node in payload["nodes"]:
            node["x"] = -node["x"]
            node["y"] = 1000 - node["y"]
        payload["edges"].reverse()
        updated = AutomationGraph.replace_from_payload(automation, payload)
        result = AutomationGraph.snapshot(updated)
        branches = sorted([e for e in result["edges"] if e["source"] == str(start.key)], key=lambda e:e["order"])
        self.assertEqual([e["target"] for e in branches], [str(first.key), str(second.key)])
        executed = []
        execute_step.side_effect = lambda action: (executed.append(action.function) or True, "ok")
        success, _ = AutomationExecutor.execute(updated)
        self.assertTrue(success)
        self.assertEqual(executed, ["power_on", "power_off"])

    def test_explicit_reorder_changes_only_edge_order(self):
        automation, start, first, second = self.ordered_graph()
        payload = AutomationGraph.snapshot(automation)
        before_nodes = payload["nodes"]
        for edge in payload["edges"]:
            if edge["source"] == str(start.key):
                edge["order"] = 2 if edge["target"] == str(first.key) else 1
        normalized = AutomationGraph.normalize_editor_payload(payload)
        branches = [e for e in normalized["edges"] if e["source"] == str(start.key)]
        self.assertEqual([e["target"] for e in branches], [str(second.key), str(first.key)])
        self.assertEqual([n["y"] for n in normalized["nodes"]], [n["y"] for n in before_nodes])

    def test_ambiguous_partial_and_duplicate_orders_are_rejected(self):
        automation, start, _, _ = self.ordered_graph()
        for invalid in [None, 1, 0, -1, True, 1.5, "2"]:
            with self.subTest(order=invalid):
                payload = AutomationGraph.snapshot(automation)
                branch = next(e for e in payload["edges"] if e["source"] == str(start.key) and e["order"] == 2)
                if invalid is None:
                    del branch["order"]
                else:
                    branch["order"] = invalid
                with self.assertRaises(GraphValidationError):
                    AutomationGraph.normalize_editor_payload(payload)

    def test_legacy_payload_without_orders_keeps_visual_fallback(self):
        automation, start, first, second = self.ordered_graph()
        payload = AutomationGraph.snapshot(automation)
        for edge in payload["edges"]:
            del edge["order"]
        normalized = AutomationGraph.normalize_editor_payload(payload)
        branches = [e for e in normalized["edges"] if e["source"] == str(start.key)]
        self.assertEqual([e["target"] for e in branches], [str(second.key), str(first.key)])


class AutomationGraphLegacyCompatibilityTests(AutomationGraphTestMixin, TestCase):
    def test_legacy_and_step_is_losslessly_grouped_in_one_condition_node(self):
        automation = Automation.objects.create(name="Legacy AND", automation_type=Automation.Type.SCHEDULED)
        step = Step.objects.create(
            automation=automation,
            order=1,
            trigger_operator=Step.TriggerOperator.AND,
        )
        first = Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={
                "device_id": self.sensor.pk,
                "device_uid": self.sensor.device_uid,
                "device_name": self.sensor.name,
                "key": "occupied",
                "operator": "eq",
                "value": True,
            },
        )
        second = Trigger.objects.create(
            step=step,
            order=2,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={
                "device_id": self.sensor.pk,
                "device_uid": self.sensor.device_uid,
                "device_name": self.sensor.name,
                "key": "door",
                "operator": "eq",
                "value": "closed",
            },
        )
        action = Action.objects.create(
            step=step,
            order=1,
            device=self.light,
            function="power_on",
        )

        self.assertTrue(AutomationGraph.sync_from_legacy(automation))
        automation.refresh_from_db()
        self.assertEqual(automation.graph_version, GRAPH_VERSION)
        condition = automation.graph_nodes.get(node_type=AutomationNode.Type.CONDITION)
        self.assertEqual(condition.conditions.count(), 2)
        self.assertEqual(
            list(condition.conditions.order_by("order").values_list("legacy_trigger_id", flat=True)),
            [first.pk, second.pk],
        )
        action_node = automation.graph_nodes.get(node_type=AutomationNode.Type.ACTION)
        self.assertEqual(action_node.actions.get().legacy_action_id, action.pk)
        AutomationGraph.validate_automation(automation)

    def test_legacy_or_step_is_expanded_to_independent_entry_conditions(self):
        automation = Automation.objects.create(name="Legacy OR", automation_type=Automation.Type.SCHEDULED)
        step = Step.objects.create(
            automation=automation,
            order=1,
            trigger_operator=Step.TriggerOperator.OR,
        )
        for order, key in enumerate(("occupied", "door"), start=1):
            Trigger.objects.create(
                step=step,
                order=order,
                trigger_type=Trigger.Type.DEVICE_STATE,
                config={
                    "device_id": self.sensor.pk,
                    "device_uid": self.sensor.device_uid,
                    "device_name": self.sensor.name,
                    "key": key,
                    "operator": "eq",
                    "value": True,
                },
            )
        Action.objects.create(step=step, order=1, device=self.light, function="power_on")

        self.assertTrue(AutomationGraph.sync_from_legacy(automation))
        conditions = list(
            automation.graph_nodes.filter(node_type=AutomationNode.Type.CONDITION).order_by("position_y")
        )
        self.assertEqual(len(conditions), 2)
        self.assertTrue(all(node.conditions.count() == 1 for node in conditions))
        start = automation.graph_nodes.get(node_type=AutomationNode.Type.START)
        self.assertEqual(start.outgoing_edges.count(), 2)
        action_node = automation.graph_nodes.get(node_type=AutomationNode.Type.ACTION)
        self.assertEqual(
            AutomationEdge.objects.filter(
                source__in=conditions,
                target=action_node,
                outcome=AutomationEdge.Outcome.TRUE,
            ).count(),
            2,
        )
        AutomationGraph.validate_automation(automation)

    def test_legacy_or_event_still_enqueues_only_one_run_per_event(self):
        automation = Automation.objects.create(
            name="Legacy OR event",
            automation_type=Automation.Type.SCHEDULED,
        )
        step = Step.objects.create(
            automation=automation,
            order=1,
            trigger_operator=Step.TriggerOperator.OR,
        )
        for order in (1, 2):
            Trigger.objects.create(
                step=step,
                order=order,
                trigger_type=Trigger.Type.MQTT_EVENT,
                config={
                    "topic": "graph/test/event",
                    "field": "value",
                    "operator": "eq" if order == 1 else "received",
                    "value": True if order == 1 else None,
                },
            )
        Action.objects.create(step=step, order=1, device=self.light, function="power_on")
        AutomationGraph.sync_from_legacy(automation)

        runs = AutomationService.process_event(
            "graph/test/event",
            {"event_id": "same-event", "value": True},
        )

        self.assertEqual(len(runs), 1)
        self.assertEqual(AutomationRun.objects.filter(step=step).count(), 1)
        self.assertEqual(AutomationRun.objects.get(step=step).graph_version, GRAPH_VERSION)

    def test_legacy_or_enters_through_a_true_branch_not_merely_event_source_branch(self):
        automation = Automation.objects.create(
            name="Legacy OR branch selection",
            automation_type=Automation.Type.SCHEDULED,
        )
        step = Step.objects.create(
            automation=automation,
            order=1,
            trigger_operator=Step.TriggerOperator.OR,
        )
        false_trigger = Trigger.objects.create(
            step=step, order=1, trigger_type=Trigger.Type.MQTT_EVENT,
            config={"topic": "graph/test/or", "field": "value", "operator": "eq", "value": False},
        )
        true_trigger = Trigger.objects.create(
            step=step, order=2, trigger_type=Trigger.Type.MQTT_EVENT,
            config={"topic": "graph/test/or", "field": "value", "operator": "eq", "value": True},
        )
        Action.objects.create(step=step, order=1, device=self.light, function="power_on")
        AutomationGraph.sync_from_legacy(automation)
        expected_entry = AutomationGraph.legacy_trigger_entry_key(automation, true_trigger.pk)
        false_entry = AutomationGraph.legacy_trigger_entry_key(automation, false_trigger.pk)
        self.assertNotEqual(expected_entry, false_entry)

        runs = AutomationService.process_event(
            "graph/test/or",
            {"event_id": "or-branch-event", "value": True},
        )

        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run.entry_node_key, expected_entry)
        self.assertEqual(run.trigger_payload.get("entry_node_key"), str(expected_entry))


class AutomationGraphCrossAutomationIsolationTests(AutomationGraphTestMixin, TestCase):
    def test_same_entry_uuid_in_two_automations_does_not_cross_deduplicate(self):
        shared_key = uuid.uuid4()
        automations = []
        for index in (1, 2):
            automation = self.make_automation(
                name=f"Shared key {index}",
                automation_type=Automation.Type.SCHEDULED,
            )
            start = self.node(automation, AutomationNode.Type.START, x=0, y=100)
            condition = AutomationNode.objects.create(
                automation=automation,
                key=shared_key,
                node_type=AutomationNode.Type.CONDITION,
                position_x=200,
                position_y=100,
            )
            action = self.node(automation, AutomationNode.Type.ACTION, x=400, y=100)
            end = self.node(automation, AutomationNode.Type.END, x=600, y=100)
            self.condition(
                condition,
                condition_type=Trigger.Type.MQTT_EVENT,
                config={
                    "topic": "graph/test/shared-key",
                    "field": "value",
                    "operator": "received",
                    "value": None,
                },
            )
            self.action(action)
            self.edge(automation, start, condition)
            self.edge(automation, condition, action, AutomationEdge.Outcome.TRUE)
            self.edge(automation, condition, end, AutomationEdge.Outcome.FALSE)
            self.edge(automation, action, end)
            automations.append(automation)

        runs = AutomationService.process_event(
            "graph/test/shared-key",
            {"event_id": "shared-event", "value": True},
        )

        self.assertEqual(len(runs), 2)
        self.assertEqual(
            set(AutomationRun.objects.filter(source_event_id="shared-event").values_list("automation_id", flat=True)),
            {automation.pk for automation in automations},
        )


class AutomationGraphRuntimeTests(AutomationGraphTestMixin, TestCase):
    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_condition_items_are_anded(self, execute_step):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        condition = self.node(automation, AutomationNode.Type.CONDITION, x=200)
        action = self.node(automation, AutomationNode.Type.ACTION, x=400)
        end = self.node(automation, AutomationNode.Type.END, x=600)
        self.condition(condition, order=1)
        self.condition(condition, order=2, config={
            "device_id": self.sensor.pk,
            "device_uid": self.sensor.device_uid,
            "device_name": self.sensor.name,
            "key": "door",
            "operator": "eq",
            "value": "closed",
        })
        self.action(action)
        self.edge(automation, start, condition)
        self.edge(automation, condition, action, AutomationEdge.Outcome.TRUE)
        self.edge(automation, condition, end, AutomationEdge.Outcome.FALSE)
        self.edge(automation, action, end)
        self.set_state("occupied", True)
        self.set_state("door", "open")

        success, _ = AutomationExecutor.execute(automation)
        self.assertTrue(success)
        execute_step.assert_not_called()

        self.set_state("door", "closed")
        success, _ = AutomationExecutor.execute(automation)
        self.assertTrue(success)
        execute_step.assert_called_once()

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_merge_target_executes_once_per_run(self, execute_step):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        cond_a = self.node(automation, AutomationNode.Type.CONDITION, x=200, y=80)
        cond_b = self.node(automation, AutomationNode.Type.CONDITION, x=200, y=260)
        action = self.node(automation, AutomationNode.Type.ACTION, x=500, y=170)
        end = self.node(automation, AutomationNode.Type.END, x=750, y=170)
        self.condition(cond_a, config={
            "device_id": self.sensor.pk, "device_uid": self.sensor.device_uid,
            "device_name": self.sensor.name, "key": "a", "operator": "eq", "value": True,
        })
        self.condition(cond_b, config={
            "device_id": self.sensor.pk, "device_uid": self.sensor.device_uid,
            "device_name": self.sensor.name, "key": "b", "operator": "eq", "value": True,
        })
        self.action(action)
        self.edge(automation, start, cond_a, order=1)
        self.edge(automation, start, cond_b, order=2)
        self.edge(automation, cond_a, action, AutomationEdge.Outcome.TRUE)
        self.edge(automation, cond_b, action, AutomationEdge.Outcome.TRUE)
        self.edge(automation, cond_a, end, AutomationEdge.Outcome.FALSE)
        self.edge(automation, cond_b, end, AutomationEdge.Outcome.FALSE, order=1)
        self.edge(automation, action, end)
        self.set_state("a", True)
        self.set_state("b", True)

        success, _ = AutomationExecutor.execute(automation)

        self.assertTrue(success)
        self.assertEqual(execute_step.call_count, 1)
        run = AutomationRun.objects.latest("id")
        self.assertEqual(run.action_runs.count(), 1)
        self.assertEqual(
            AutomationNodeRun.objects.filter(automation_run=run, node_key=action.key).count(),
            1,
        )

    def test_action_node_is_graph_success_when_actionrun_is_queued(self):
        automation = self.make_automation()
        start, action, _end = self.simple_graph(automation)
        run = AutomationExecutor.enqueue(automation)

        AutomationExecutor._plan_next_run(root_run_id=run.pk)
        # START
        node_run = AutomationExecutor._claim_next_graph_node(root_run_id=run.pk)
        self.assertEqual(node_run.node_key, start.key)
        AutomationExecutor._execute_graph_node(node_run)
        # ACTION: queue registration is the graph control-flow boundary.
        node_run = AutomationExecutor._claim_next_graph_node(root_run_id=run.pk)
        self.assertEqual(node_run.node_key, action.key)
        AutomationExecutor._execute_graph_node(node_run)

        node_run.refresh_from_db()
        queued = run.action_runs.get()
        self.assertEqual(node_run.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(queued.status, AutomationRun.Status.PENDING)

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_delay_node_pauses_control_flow_without_blocking_prior_action(self, execute_step):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        first = self.node(automation, AutomationNode.Type.ACTION, x=200)
        delay = self.node(automation, AutomationNode.Type.DELAY, x=400, delay=3600)
        second = self.node(automation, AutomationNode.Type.ACTION, x=600)
        end = self.node(automation, AutomationNode.Type.END, x=800)
        self.action(first, function="power_on")
        self.action(second, function="power_off")
        self.edge(automation, start, first)
        self.edge(automation, first, delay)
        self.edge(automation, delay, second)
        self.edge(automation, second, end)

        success, message = AutomationExecutor.execute(automation)

        self.assertTrue(success)
        self.assertIn("지연", message)
        self.assertEqual(execute_step.call_count, 1)
        run = AutomationRun.objects.latest("id")
        waiting = run.node_runs.get(node_key=delay.key)
        self.assertEqual(waiting.status, AutomationRun.Status.WAITING)
        self.assertEqual(run.action_runs.count(), 1)

        waiting.available_at = timezone.now() - timedelta(seconds=1)
        waiting.save(update_fields=["available_at"])
        for _ in range(10):
            if AutomationExecutor.run_next_pending(root_run_id=run.pk) is None:
                break
        run.refresh_from_db()
        self.assertEqual(execute_step.call_count, 2)
        self.assertEqual(run.status, AutomationRun.Status.SUCCESS)

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_graph_snapshot_does_not_depend_on_legacy_action_row_after_enqueue(self, execute_step):
        automation = Automation.objects.create(
            name="Legacy snapshot isolation",
            automation_type=Automation.Type.IMMEDIATE,
        )
        step = Step.objects.create(automation=automation, order=1)
        legacy_action = Action.objects.create(
            step=step, order=1, device=self.light, function="power_on"
        )
        self.assertTrue(AutomationGraph.sync_from_legacy(automation))
        run = AutomationExecutor.enqueue(automation)
        self.assertTrue(run.graph_snapshot)
        snapshot_item = next(
            item
            for node in run.graph_snapshot["nodes"]
            if node["type"] == AutomationNode.Type.ACTION
            for item in node["actions"]
        )
        self.assertEqual(snapshot_item["legacy_action_id"], legacy_action.pk)

        # Emergency use of the legacy editor can rebuild/delete old Step rows
        # while a graph run is waiting. Snapshot execution must remain isolated.
        legacy_action.delete()
        success, _ = AutomationExecutor.execute_run(run)

        self.assertTrue(success)
        queued = run.action_runs.get()
        self.assertIsNone(queued.action_id)
        self.assertEqual(queued.function, "power_on")
        execute_step.assert_called_once()

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_run_uses_graph_snapshot_after_live_graph_is_edited(self, execute_step):
        automation = self.make_automation()
        _start, action, _end = self.simple_graph(automation, action_function="power_on")
        run = AutomationExecutor.enqueue(automation)
        snapshot_revision = run.graph_snapshot["revision"]

        item = action.actions.get()
        item.function = "power_off"
        item.save(update_fields=["function"])
        automation.graph_revision += 1
        automation.save(update_fields=["graph_revision"])

        success, _ = AutomationExecutor.execute_run(run)

        self.assertTrue(success)
        self.assertEqual(snapshot_revision, 1)
        executed_action_run = run.action_runs.get()
        self.assertEqual(executed_action_run.function, "power_on")
        self.assertEqual(action.actions.get().function, "power_off")
        execute_step.assert_called_once()

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(False, "device failure"))
    def test_action_failure_cancels_queued_downstream_work(self, execute_step):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        first = self.node(automation, AutomationNode.Type.ACTION, x=200)
        second = self.node(automation, AutomationNode.Type.ACTION, x=400)
        end = self.node(automation, AutomationNode.Type.END, x=600)
        self.action(first, function="power_on")
        self.action(second, function="power_off")
        self.edge(automation, start, first)
        self.edge(automation, first, second)
        self.edge(automation, second, end)

        success, _ = AutomationExecutor.execute(automation)

        self.assertFalse(success)
        self.assertEqual(execute_step.call_count, 1)
        run = AutomationRun.objects.latest("id")
        run.refresh_from_db()
        self.assertEqual(run.status, AutomationRun.Status.FAILED)
        statuses = list(run.action_runs.order_by("id").values_list("status", flat=True))
        self.assertEqual(statuses, [AutomationRun.Status.FAILED, AutomationRun.Status.CANCELLED])

    @patch("iotcore.scheduler.executor.DeviceService.execute_step", return_value=(True, "ok"))
    def test_multiple_actions_inside_one_node_keep_item_order(self, execute_step):
        automation = self.make_automation()
        start = self.node(automation, AutomationNode.Type.START)
        action = self.node(automation, AutomationNode.Type.ACTION, x=300)
        end = self.node(automation, AutomationNode.Type.END, x=600)
        self.action(action, order=1, function="power_on")
        self.action(action, order=2, function="power_off")
        self.edge(automation, start, action)
        self.edge(automation, action, end)

        with patch.object(AutomationExecutor, "DEFAULT_ACTION_DELAY", 0):
            success, _ = AutomationExecutor.execute(automation)

        self.assertTrue(success)
        run = AutomationRun.objects.latest("id")
        self.assertEqual(
            list(run.action_runs.order_by("id").values_list("function", flat=True)),
            ["power_on", "power_off"],
        )
        self.assertEqual(execute_step.call_count, 2)

    def test_weekly_recurring_entry_creates_new_run_per_scheduled_occurrence(self):
        automation = self.make_automation(
            name="Weekly graph",
            automation_type=Automation.Type.SCHEDULED,
        )
        start = self.node(automation, AutomationNode.Type.START)
        condition = self.node(automation, AutomationNode.Type.CONDITION, x=200)
        action = self.node(automation, AutomationNode.Type.ACTION, x=400)
        end = self.node(automation, AutomationNode.Type.END, x=600)
        item = self.condition(
            condition,
            condition_type=Trigger.Type.SCHEDULE,
            config={
                "schedule_type": Trigger.ScheduleType.WEEKLY,
                "weekdays": [0, 1, 2, 3, 4, 5, 6],
                "time_mode": Trigger.ScheduleTimeMode.AT,
                "time": "08:00",
            },
        )
        self.action(action)
        self.edge(automation, start, condition)
        self.edge(automation, condition, action, AutomationEdge.Outcome.TRUE)
        self.edge(automation, condition, end, AutomationEdge.Outcome.FALSE)
        self.edge(automation, action, end)
        first_at = timezone.now().replace(microsecond=0)
        second_at = first_at + timedelta(days=1)

        with transaction.atomic():
            first = AutomationService._enqueue_graph_node_locked(
                condition,
                now=first_at,
                source=AutomationRun.Source.SCHEDULER,
                scheduled_for=first_at,
                trigger_payload={"source_trigger_id": item.pk},
            )
        condition.refresh_from_db()
        with transaction.atomic():
            second = AutomationService._enqueue_graph_node_locked(
                condition,
                now=second_at,
                source=AutomationRun.Source.SCHEDULER,
                scheduled_for=second_at,
                trigger_payload={"source_trigger_id": item.pk},
            )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(AutomationRun.objects.filter(automation=automation).count(), 2)

    def test_nested_graph_automation_cycle_fails_safely(self):
        a = self.make_automation(name="A")
        b = self.make_automation(name="B")
        a_start = self.node(a, AutomationNode.Type.START)
        a_action = self.node(a, AutomationNode.Type.ACTION)
        a_end = self.node(a, AutomationNode.Type.END)
        self.action(a_action, target=b)
        self.edge(a, a_start, a_action)
        self.edge(a, a_action, a_end)

        b_start = self.node(b, AutomationNode.Type.START)
        b_action = self.node(b, AutomationNode.Type.ACTION)
        b_end = self.node(b, AutomationNode.Type.END)
        self.action(b_action, target=a)
        self.edge(b, b_start, b_action)
        self.edge(b, b_action, b_end)

        success, message = AutomationExecutor.execute(a)

        self.assertFalse(success)
        root = AutomationRun.objects.filter(automation=a, root_run__isnull=True).latest("id")
        root.refresh_from_db()
        self.assertEqual(root.status, AutomationRun.Status.FAILED)
        self.assertTrue(
            AutomationRun.objects.filter(message__contains="순환").exists(),
            "순환 참조를 감지한 하위 Run 기록이 남아야 합니다.",
        )
        self.assertLessEqual(AutomationRun.objects.count(), 3)


class AutomationGraphNestedCompletionTests(AutomationGraphTestMixin, TestCase):
    def graph_calling(self, target):
        parent = self.make_automation(name="Graph parent")
        start = self.node(parent, AutomationNode.Type.START)
        action = self.node(parent, AutomationNode.Type.ACTION)
        end = self.node(parent, AutomationNode.Type.END)
        self.action(action, target=target)
        self.edge(parent, start, action)
        self.edge(parent, action, end)
        return parent

    @patch("iotcore.scheduler.executor.DeviceService.execute_step")
    def test_empty_legacy_child_failure_cancels_waiting_graph_nodes(self, execute_step):
        child = Automation.objects.create(name="Empty legacy child")
        parent = self.graph_calling(child)
        action = parent.graph_nodes.get(node_type=AutomationNode.Type.ACTION)
        end = parent.graph_nodes.get(node_type=AutomationNode.Type.END)
        parent.graph_edges.filter(source=action, target=end).delete()
        delay = self.node(parent, AutomationNode.Type.DELAY, delay=3600)
        downstream = self.node(parent, AutomationNode.Type.ACTION)
        self.action(downstream)
        self.edge(parent, action, delay)
        self.edge(parent, delay, downstream)
        self.edge(parent, downstream, end)

        success, _ = AutomationExecutor.execute(parent)

        self.assertFalse(success)
        root = AutomationRun.objects.get(automation=parent, root_run__isnull=True)
        self.assertEqual(root.status, AutomationRun.Status.FAILED)
        self.assertEqual(root.node_runs.get(node_key=delay.key).status, AutomationRun.Status.CANCELLED)
        self.assertFalse(root.node_runs.filter(node_key=downstream.key).exists())
        self.assertFalse(AutomationRun.objects.filter(status__in=AutomationExecutor.ACTIVE_STATUSES).exists())
        execute_step.assert_not_called()

    @patch("iotcore.scheduler.executor.DeviceService.execute_step")
    def test_unmatched_legacy_child_finishes_graph_parent(self, execute_step):
        child = Automation.objects.create(name="Unmatched legacy child")
        step = Step.objects.create(automation=child, order=1)
        Action.objects.create(step=step, order=1, device=self.light, function="power_on")
        Trigger.objects.create(
            step=step,
            order=1,
            trigger_type=Trigger.Type.DEVICE_STATE,
            config={"device_id": self.sensor.pk, "device_uid": self.sensor.device_uid,
                    "key": "occupied", "operator": "eq", "value": True},
        )
        self.set_state("occupied", False)
        parent = self.graph_calling(child)

        success, _ = AutomationExecutor.execute(parent)

        self.assertTrue(success)
        root = AutomationRun.objects.get(automation=parent, root_run__isnull=True)
        self.assertEqual(root.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(root.action_runs.get().status, AutomationRun.Status.SUCCESS)
        self.assertFalse(AutomationRun.objects.filter(status__in=AutomationExecutor.ACTIVE_STATUSES).exists())
        execute_step.assert_not_called()


class AutomationGraphExecutionPanelTests(AutomationGraphTestMixin, TestCase):
    def test_execution_panel_exposes_graph_delay_wait(self):
        from .templatetags.execution_status import execution_status_snapshot

        automation = self.make_automation(name="Delay panel")
        delay = self.node(
            automation,
            AutomationNode.Type.DELAY,
            title="에어컨 명령 후 안정화 대기",
            delay=30,
        )
        run = AutomationRun.objects.create(
            automation=automation,
            automation_name=automation.name,
            graph_version=GRAPH_VERSION,
            status=AutomationRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        node_run = AutomationNodeRun.objects.create(
            automation_run=run,
            node=delay,
            node_key=delay.key,
            node_type=AutomationNode.Type.DELAY,
            queue_order=1,
            status=AutomationRun.Status.WAITING,
            available_at=timezone.now() + timedelta(seconds=30),
        )

        snapshot = execution_status_snapshot(limit=5)

        displayed = next(item for item in snapshot["running_runs"] if item.pk == run.pk)
        self.assertEqual(displayed.waiting_node.pk, node_run.pk)
        self.assertEqual(displayed.waiting_node.node.title, "에어컨 명령 후 안정화 대기")

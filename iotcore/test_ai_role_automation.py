from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from .ai.pc_actions import PC_AI_ACTIONS
from .ai.service import AIControlService
from .automation_graph import AutomationGraph, GRAPH_VERSION
from .device_actions import DeviceActionRegistry
from .device.services.device_service import DeviceService
from .models import (AIServiceRoute, Action, ActionRun, Automation, Device, Step,
                     AutomationNode, AutomationEdge, AutomationActionItem)
from .scheduler.executor import AutomationExecutor


class AIRoleAutomationTests(TestCase):
    def setUp(self):
        self.main, _ = Device.objects.update_or_create(device_uid='home-ai-main', defaults={
            'name':'Main','device_type':'pc','device_role':'control','protocol':'tcpip',
            'control_config':{'ip_address':'192.168.0.4'}})
        self.sub, _ = Device.objects.update_or_create(device_uid='home-ai-sub1', defaults={
            'name':'Sub1','device_type':'pc','device_role':'control','protocol':'tcpip',
            'control_config':{'ip_address':'192.168.0.2'}})
        for service in ('asr','nlp'):
            AIServiceRoute.objects.create(service=service,configured=True,active_device=self.sub,standby_device=self.main)

    def run_action(self, device, action):
        return DeviceService.execute_step(SimpleNamespace(device=device,function=action,parameter=None))

    def test_graph_picker_accepts_all_roles_without_json_parameters(self):
        codes={action.code for action in DeviceActionRegistry.get_actions('pc')}
        self.assertEqual(len(PC_AI_ACTIONS),9)
        self.assertTrue(set(PC_AI_ACTIONS)<=codes)
        self.assertTrue({'power_on','power_off'}<=codes)
        for action in PC_AI_ACTIONS:
            normalized=AutomationGraph._normalize_action({'device_id':self.main.pk,'function':action})
            self.assertEqual(normalized['function'],action)

    @patch('iotcore.device.services.device_service.WOLClient.send_wol')
    @patch('iotcore.device.services.device_service.requests.post')
    def test_active_swap_is_model_specific_and_sends_no_pc_commands(self,post,wol):
        ok,message=self.run_action(self.main,'ai_asr_active')
        self.assertTrue(ok,message)
        route=AIServiceRoute.objects.get(pk='asr')
        self.assertEqual((route.active_device_id,route.standby_device_id),(self.main.pk,self.sub.pk))
        self.assertEqual(AIServiceRoute.objects.get(pk='nlp').active_device_id,self.sub.pk)
        post.assert_not_called()
        wol.assert_not_called()

    def test_standby_assignment_and_removal_are_supported(self):
        AIServiceRoute.objects.filter(pk='asr').update(standby_device=None)
        self.assertTrue(self.run_action(self.main,'ai_asr_standby')[0])
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').standby_device_id,self.main.pk)
        self.assertTrue(self.run_action(self.main,'ai_asr_off')[0])
        self.assertIsNone(AIServiceRoute.objects.get(pk='asr').standby_device_id)
        revision=AIServiceRoute.objects.get(pk='asr').revision
        self.assertTrue(self.run_action(self.main,'ai_asr_off')[0])
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').revision,revision)

    def test_active_unassignment_promotes_standby_and_last_active_is_preserved(self):
        self.assertTrue(self.run_action(self.sub,'ai_asr_off')[0])
        route=AIServiceRoute.objects.get(pk='asr')
        self.assertEqual(route.active_device_id,self.main.pk)
        self.assertIsNone(route.standby_device_id)
        ok,message=self.run_action(self.main,'ai_asr_off')
        self.assertFalse(ok)
        self.assertIn('최소 한 대',message)
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.main.pk)

    def test_tts_only_saves_placement_and_off_is_idempotent_when_unconfigured(self):
        self.assertTrue(self.run_action(self.main,'ai_tts_off')[0])
        self.assertFalse(AIServiceRoute.objects.filter(pk='tts').exists())
        ok,message=self.run_action(self.main,'ai_tts_active')
        self.assertTrue(ok,message)
        self.assertIn('배치만 저장',message)
        self.assertEqual(AIServiceRoute.objects.get(pk='tts').active_device_id,self.main.pk)

    def test_queue_executor_records_role_change_success(self):
        automation=Automation.objects.create(name='Switch ASR',automation_type='immediate')
        step=Step.objects.create(automation=automation,order=1)
        Action.objects.create(step=step,order=1,device=self.main,function='ai_asr_active')
        success,message=AutomationExecutor.execute(automation)
        self.assertTrue(success,message)
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.main.pk)
        event=ActionRun.objects.get(root_run__automation=automation)
        self.assertEqual(event.status,'success')
        self.assertIn('ASR',event.message)

    def test_invalid_pc_configuration_fails_without_changing_route(self):
        self.main.control_config={}
        self.main.save()
        ok,message=self.run_action(self.main,'ai_asr_active')
        self.assertFalse(ok)
        self.assertIn('주소',message)
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.sub.pk)

    def test_queue_records_failure_when_removing_the_last_active(self):
        AIServiceRoute.objects.filter(pk='asr').update(active_device=self.main,standby_device=None)
        automation=Automation.objects.create(name='Invalid unassignment',automation_type='immediate')
        step=Step.objects.create(automation=automation,order=1)
        Action.objects.create(step=step,order=1,device=self.main,function='ai_asr_off')
        success,message=AutomationExecutor.execute(automation)
        self.assertFalse(success)
        event=ActionRun.objects.get(root_run__automation=automation)
        self.assertEqual(event.status,'failed')
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.main.pk)

    def test_graph_action_executes_placement_change_through_real_queue(self):
        automation=Automation.objects.create(name='Graph ASR placement',automation_type='immediate',
                                            graph_version=GRAPH_VERSION,graph_revision=1)
        start=AutomationNode.objects.create(automation=automation,node_type='start')
        action=AutomationNode.objects.create(automation=automation,node_type='action')
        end=AutomationNode.objects.create(automation=automation,node_type='end')
        AutomationEdge.objects.create(automation=automation,source=start,target=action)
        AutomationEdge.objects.create(automation=automation,source=action,target=end)
        AutomationActionItem.objects.create(node=action,order=1,device=self.main,
                                            action_type='device',function='ai_asr_active')
        success,message=AutomationExecutor.execute(automation)
        self.assertTrue(success,message)
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.main.pk)
        self.assertEqual(ActionRun.objects.get(root_run__automation=automation).status,'success')

    def test_direct_ai_api_cannot_bypass_staff_routing_endpoint(self):
        with self.assertRaises(ValidationError):
            AIControlService.execute_device_action(device=self.main,function='ai_asr_active')
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.sub.pk)

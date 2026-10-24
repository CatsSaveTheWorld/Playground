import io,json
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse

from .ai.asr_client import ASRClient
from .ai.nlp_client import NLPClient
from .ai.routing import snapshot, assign_role, probe_device
from .api.views.voice import voice_upload
from .models import AIServiceRoute, AIInferenceEvent, Device


def reply(payload, status=200):
    response = Mock(ok=200 <= status < 300, status_code=status, text='test worker response')
    response.json.return_value = payload
    return response


@override_settings(VOICE_ASR_ENABLED=True, VOICE_NLP_ENABLED=True,
    VOICE_ASR_BASE_URL='http://legacy.invalid:6101', VOICE_NLP_BASE_URL='http://legacy.invalid:6201', VOICE_SAVE_UPLOADS=False)
class AIRoutingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.main = Device.objects.get(device_uid='home-ai-main')
        self.sub = Device.objects.get(device_uid='home-ai-sub1')
        for service in ('asr','nlp','tts'):
            AIServiceRoute.objects.create(service=service, active_device=self.sub, standby_device=self.main, configured=True)
        self.user = get_user_model().objects.create_user(username='routing-admin',is_staff=True)
        self.client.force_login(self.user)

    def transcribe(self, client=None):
        return (client or ASRClient()).transcribe(audio_bytes=b'RIFF-test', request_id='a'*32)

    def voice(self):
        audio = SimpleUploadedFile('voice.wav', b'RIFF\x04\x00\x00\x00WAVEtest')
        return voice_upload(RequestFactory().post('/iotcore/voice/upload/', {'audio':audio}))

    def test_preferred_sub1_overrides_legacy_settings(self):
        with patch('iotcore.ai.asr_client.requests.post',return_value=reply({'ok':True,'text':'test'})) as post:
            result=self.transcribe()
        self.assertTrue(result.ok)
        self.assertEqual(post.call_args.args[0],'http://192.168.0.2:6101/v1/asr/transcribe')
        self.assertEqual(result.node_uid,'home-ai-sub1')
        self.assertFalse(result.used_standby)

    def test_timeout_falls_back_once_and_monitor_tracks_actual_nodes(self):
        with patch('iotcore.ai.asr_client.requests.post',side_effect=[requests.Timeout('unreachable'),reply({'ok':True,'text':'test'})]) as post:
            result=self.transcribe()
        self.assertEqual(post.call_count,2)
        self.assertEqual(post.call_args.args[0],'http://192.168.0.4:6101/v1/asr/transcribe')
        self.assertTrue(result.used_standby)
        self.assertEqual(result.node_uid,'home-ai-main')
        self.assertEqual(list(AIInferenceEvent.objects.order_by('id').values_list('node_uid','status')),
                         [('home-ai-sub1','failed'),('home-ai-main','success')])
        self.assertEqual(snapshot()['asr']['last_result']['status'],'standby')

    def test_file_is_reopened_for_failover(self):
        from tempfile import NamedTemporaryFile
        from pathlib import Path
        with NamedTemporaryFile(delete=False) as stream:
            stream.write(b'audio-test'); name=Path(stream.name)
        seen=[]
        def send(*args,**kwargs):
            seen.append(kwargs['files']['audio'][1].read())
            if len(seen)==1: raise requests.ConnectionError('offline')
            return reply({'ok':True,'text':'test'})
        try:
            with patch('iotcore.ai.asr_client.requests.post',side_effect=send):
                result=ASRClient().transcribe(audio_path=name,request_id='b'*32)
        finally: name.unlink()
        self.assertTrue(result.ok)
        self.assertEqual(seen,[b'audio-test',b'audio-test'])

    def test_model_assignments_are_independent(self):
        assign_role('nlp',self.main.pk,'active',1)
        with patch('iotcore.ai.nlp_client.requests.post',return_value=reply({'ok':True,'text':'test','intent':''})) as post:
            NLPClient().parse(text='test',request_id='b'*32)
        self.assertEqual(post.call_args.args[0],'http://192.168.0.4:6201/v1/nlp/parse')
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.sub.pk)

    def test_503_uses_standby(self):
        with patch('iotcore.ai.asr_client.requests.post',side_effect=[reply({'detail':'loading'},503),reply({'ok':True,'text':'ok'})]):
            result=self.transcribe()
        self.assertTrue(result.ok)
        self.assertTrue(result.used_standby)

    def test_bad_json_response_uses_standby(self):
        broken=reply({},200);broken.json.side_effect=ValueError('bad JSON')
        with patch('iotcore.ai.asr_client.requests.post',side_effect=[broken,reply({'ok':True,'text':'ok'})]):
            self.assertTrue(self.transcribe().used_standby)

    def test_client_error_is_not_worker_failover(self):
        with patch('iotcore.ai.asr_client.requests.post',return_value=reply({'detail':'bad audio'},422)) as post:
            result=self.transcribe()
        self.assertEqual(post.call_count,1)
        self.assertFalse(result.unavailable)

    def test_unknown_nlp_command_is_not_failover(self):
        with patch('iotcore.ai.nlp_client.requests.post',return_value=reply({'ok':False,'text':'unknown','error':'unsupported'})) as post:
            result=NLPClient().parse(text='unknown',request_id='c'*32)
        self.assertEqual(post.call_count,1)
        self.assertFalse(result.unavailable)

    def test_both_asr_workers_down_returns_503_without_execution(self):
        with patch('iotcore.ai.asr_client.requests.post',side_effect=requests.ConnectionError('offline')) as post, \
             patch('iotcore.api.views.voice.AIControlService.execute_device_action') as device, \
             patch('iotcore.api.views.voice.AIControlService.run_automation') as automation:
            response=self.voice()
        data=json.loads(response.content)
        self.assertEqual(response.status_code,503)
        self.assertEqual(response['Retry-After'],'5')
        self.assertEqual(data['stage'],'asr')
        self.assertEqual(data['error'],'ai_workers_unavailable')
        self.assertEqual(len(data['attempts']),2)
        self.assertEqual(post.call_count,2)
        self.assertIsNone(data['execution'])
        device.assert_not_called();automation.assert_not_called()

    def test_both_nlp_workers_down_does_not_resolve_or_execute_device(self):
        with patch('iotcore.ai.asr_client.requests.post',side_effect=[reply({'ok':True,'text':'unknown direct command'}),requests.Timeout('offline'),requests.Timeout('offline')]) as post, \
             patch('iotcore.api.views.voice.AIControlService.resolve_device') as resolve, \
             patch('iotcore.api.views.voice.AIControlService.run_automation') as automation:
            response=self.voice()
        self.assertEqual(response.status_code,503)
        self.assertEqual(json.loads(response.content)['stage'],'nlp')
        self.assertEqual(post.call_count,3)
        resolve.assert_not_called();automation.assert_not_called()

    def test_disabled_failover_only_attempts_active(self):
        AIServiceRoute.objects.filter(pk='asr').update(failover_enabled=False)
        with patch('iotcore.ai.asr_client.requests.post',side_effect=requests.Timeout('offline')) as post:
            result=self.transcribe()
        self.assertEqual(post.call_count,1)
        self.assertTrue(result.unavailable)

    def test_existing_client_is_not_mutated_and_reads_saved_selection(self):
        client=ASRClient()
        with patch('iotcore.ai.asr_client.requests.post',return_value=reply({'ok':True,'text':'ok'})) as post:
            self.transcribe(client)
            assign_role('asr',self.main.pk,'active',1)
            client.transcribe(audio_bytes=b'test',request_id='d'*32)
        self.assertEqual(client.base_url,'http://legacy.invalid:6101')
        self.assertEqual(post.call_args.args[0],'http://192.168.0.4:6101/v1/asr/transcribe')

    def test_role_switch_swaps_the_other_card_and_persists(self):
        response=self.client.post(reverse('iotcore:ai_routing_assign'),{'device_id':self.main.pk,'service':'asr','role':'active','revision':1})
        self.assertEqual(response.status_code,200)
        route=AIServiceRoute.objects.get(pk='asr')
        self.assertEqual((route.active_device_id,route.standby_device_id),(self.main.pk,self.sub.pk))
        state=self.client.get(reverse('iotcore:ai_routing_state')).json()['routes']['asr']
        self.assertEqual(state['active']['id'],self.main.pk)
        self.assertEqual(state['revision'],2)

    def test_stale_edit_is_conflict_and_does_not_overwrite(self):
        assign_role('asr',self.main.pk,'active',1)
        response=self.client.post(reverse('iotcore:ai_routing_assign'),{'device_id':self.sub.pk,'service':'asr','role':'active','revision':1})
        self.assertEqual(response.status_code,409)
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.main.pk)

    def test_cannot_leave_all_nodes_inactive(self):
        assign_role('asr',self.main.pk,'off',1)
        response=self.client.post(reverse('iotcore:ai_routing_assign'),{'device_id':self.sub.pk,'service':'asr','role':'off','revision':2})
        self.assertEqual(response.status_code,400)
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.sub.pk)

    def test_nonstaff_and_csrf_and_method_are_enforced(self):
        url=reverse('iotcore:ai_routing_assign')
        self.assertEqual(self.client.get(url).status_code,405)
        self.user.is_staff=False;self.user.save()
        self.assertEqual(self.client.post(url,{}).status_code,403)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post(url,{}).status_code,403)
        self.client.logout();self.assertEqual(self.client.get(reverse('iotcore:ai_routing_state')).status_code,302)

    def test_invalid_device_and_url_configuration_rejected(self):
        light=Device.objects.create(device_uid='not-pc',device_type='light',name='Light')
        data={'device_id':light.pk,'service':'asr','role':'active','revision':1}
        self.assertEqual(self.client.post(reverse('iotcore:ai_routing_assign'),data).status_code,400)
        self.main.control_config={'ip_address':'http://bad.example/path'};self.main.save()
        data['device_id']=self.main.pk
        self.assertEqual(self.client.post(reverse('iotcore:ai_routing_assign'),data).status_code,400)

    def test_tts_selection_is_saved_without_inventing_endpoint(self):
        assign_role('tts',self.main.pk,'active',1)
        data=snapshot()['tts']
        self.assertTrue(data['pending'])
        self.assertEqual(data['active']['id'],self.main.pk)
        self.assertEqual(data['active']['url'],'')

    def test_probe_checks_service_identity_and_never_probes_tts(self):
        with patch('iotcore.ai.routing.requests.get',side_effect=[reply({'ok':True,'service':'unexpected'}),reply({'ok':True,'service':'iotcore-nlp-worker'})]) as get:
            health=probe_device(self.sub)
        self.assertEqual(get.call_count,2)
        self.assertEqual(health['asr']['status'],'unavailable')
        self.assertEqual(health['nlp']['status'],'ready')
        self.assertEqual(health['tts']['status'],'pending')

    def test_initializer_keeps_existing_choices(self):
        assign_role('asr',self.main.pk,'active',1)
        call_command('configure_ai_routing',stdout=io.StringIO())
        self.assertEqual(AIServiceRoute.objects.get(pk='asr').active_device_id,self.main.pk)

    def test_controls_render_below_power_buttons(self):
        with patch('iotcore.api.views.detail.MusicAssistantClient.get_playlists',return_value=([],None)):
            response=self.client.get(reverse('iotcore:device_control'))
        self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertIn('ASR 요청 역할',html)
        self.assertIn('TTS 요청 역할',html)
        self.assertLess(html.index('전원 끄기'),html.index('AI 요청 배치'))

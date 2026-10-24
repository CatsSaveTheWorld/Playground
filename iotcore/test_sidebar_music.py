from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from iotcore.infrastructure.music_assistant.client import MusicAssistantClient
from iotcore.models import Device
from iotcore.templatetags.sidebar_music import sidebar_music


class SidebarMusicTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='music-test')
        self.device = Device.objects.create(name='Test speaker', device_uid='test-player',
                                           device_type='speaker', location='Test')
        self.url = reverse('iotcore:speaker_snapshot', kwargs={'device_id': self.device.pk})
        self.client.force_login(self.user)

    def test_authentication_and_get_only(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    @patch.object(MusicAssistantClient, 'get_playlists', return_value=([{'item_id': '1', 'name': 'Warm'}], None))
    def test_playlists_are_read_without_device_commands(self, playlists):
        with patch('iotcore.api.views.speaker.DeviceService.control') as control:
            response = self.client.get(self.url, {'section': 'playlists'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['playlists'][0]['name'], 'Warm')
        self.assertIn('no-store', response['Cache-Control'])
        control.assert_not_called()

    @patch.object(MusicAssistantClient, 'get_playlists', return_value=([], 'Music server unavailable'))
    def test_playlist_errors_are_actionable(self, playlists):
        response = self.client.get(self.url, {'section': 'playlists'})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json()['success'])

    @patch.object(MusicAssistantClient, 'get_queue_state', return_value=({'state': 'playing',
        'shuffle_enabled': True, 'repeat_mode': 'one', 'current_item': {'media_item': {'name': 'Track'}}}, None))
    @patch.object(MusicAssistantClient, 'get_player_state', return_value=({'available': True, 'volume_level': 0}, None))
    @patch.object(MusicAssistantClient, 'resolve_player_id', return_value=('resolved-player', None))
    def test_current_state_includes_zero_volume_and_modes(self, resolve, player, queue):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        state = response.json()['state']
        self.assertEqual(state['volume'], 0)
        self.assertEqual(state['track'], 'Track')
        self.assertTrue(state['shuffle'])
        self.assertEqual(state['repeat'], 'one')
        resolve.assert_called_once_with(player_id='test-player', player_name='Test speaker')
        player.assert_called_once_with('resolved-player')

    @patch.object(MusicAssistantClient, 'get_queue_state', return_value=(None, 'Queue unavailable'))
    @patch.object(MusicAssistantClient, 'get_player_state', return_value=({'state': 'idle', 'volume_level': 42}, None))
    @patch.object(MusicAssistantClient, 'resolve_player_id', return_value=('resolved-player', None))
    def test_partial_state_does_not_claim_unknown_modes_are_off(self, resolve, player, queue):
        data = self.client.get(self.url).json()
        self.assertEqual(data['state']['volume'], 42)
        self.assertIsNone(data['state']['shuffle'])
        self.assertIsNone(data['state']['repeat'])
        self.assertEqual(data['message'], 'Queue unavailable')

    @patch.object(MusicAssistantClient, 'resolve_player_id', return_value=(None, 'Player unavailable'))
    def test_unavailable_player(self, resolve):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['message'], 'Player unavailable')

    @patch.object(MusicAssistantClient, 'get_playlists')
    def test_unknown_and_noncontrollable_devices_are_rejected(self, playlists):
        self.assertEqual(self.client.get(reverse('iotcore:speaker_snapshot', kwargs={'device_id': 999999})).status_code, 404)
        for values in [{'device_role': Device.Role.SENSOR}, {'device_type': 'light', 'device_role': Device.Role.CONTROL}]:
            Device.objects.filter(pk=self.device.pk).update(**values)
            self.assertEqual(self.client.get(self.url, {'section': 'playlists'}).status_code, 400)
        playlists.assert_not_called()

    @patch.object(MusicAssistantClient, 'get_playlists')
    def test_global_tag_excludes_sensors_and_never_waits_for_music_server(self, playlists):
        Device.objects.create(name='Sensor speaker', device_uid='sensor', device_type='speaker', device_role=Device.Role.SENSOR)
        request = RequestFactory().get('/iotcore/settings/')
        request.user = self.user
        context = sidebar_music({'request': request})
        self.assertEqual([item['name'] for item in context['music_speakers']], ['Test speaker'])
        self.assertEqual(context['music_speakers'][0]['urls']['snapshot'], self.url)
        playlists.assert_not_called()

    def test_widget_is_on_shell_pages_and_original_controller_is_retained(self):
        request = RequestFactory().get('/iotcore/settings/')
        request.user = self.user
        html = render_to_string('iotcore/settings.html', {'request': request})
        self.assertIn('iotSidebarMusic', html)
        self.assertIn('iotMusicToggle', html)
        self.assertIn('iotMusicRepeat', html)
        self.assertIn('iotMusicPlaylist', html)
        self.assertIn('iotcore-sidebar-music.js', html)
        detail = render_to_string('iotcore/device_control.html', {'request': request,
            'speaker_devices': [self.device], 'playlists': [{'item_id': '1', 'name': 'Warm'}]})
        self.assertIn('speaker-card-header', detail)
        self.assertIn('sendSpeakerRepeat', detail)
        self.assertIn('iotSidebarMusic', detail)

    def test_anonymous_render_does_not_expose_devices(self):
        self.assertFalse(sidebar_music({'request': SimpleNamespace(user=SimpleNamespace(is_authenticated=False))})['music_visible'])

    def test_empty_speaker_list_has_clear_message(self):
        Device.objects.all().delete()
        request = RequestFactory().get('/iotcore/settings/')
        request.user = self.user
        html = render_to_string('iotcore/settings.html', {'request': request})
        self.assertIn('등록된 스피커가 없습니다.', html)

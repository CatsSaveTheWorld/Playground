from django import template
from django.urls import reverse

from iotcore.device.repositories.device_repository import DeviceRepository


register = template.Library()


@register.inclusion_tag('iotcore/partials/sidebar_music.html', takes_context=True)
def sidebar_music(context):
    """Keep page rendering local; the browser loads Music Assistant data later."""
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return {'music_visible': False}
    devices = DeviceRepository.get_controllable().filter(device_type='speaker').order_by('name', 'id')
    speakers = []
    for device in devices:
        kwargs = {'device_id': device.pk}
        urls = {action: reverse('iotcore:' + name, kwargs=kwargs) for action, name in {
            'snapshot': 'speaker_snapshot', 'previous': 'speaker_play_previous',
            'resume': 'speaker_resume', 'pause': 'speaker_pause', 'next': 'speaker_play_next',
            'volume': 'speaker_adjust_music_volume', 'shuffleOn': 'speaker_shuffle_activate',
            'shuffleOff': 'speaker_shuffle_deactivate',
        }.items()}
        urls['playlist'] = reverse('iotcore:speaker_play_playlist', kwargs={**kwargs, 'playlist_id': '__playlist__'})
        urls['repeat'] = reverse('iotcore:speaker_set_repeat', kwargs={**kwargs, 'repeat_mode': '__repeat__'})
        speakers.append({'id': str(device.pk), 'name': device.name, 'urls': urls})
    return {'music_visible': True, 'music_speakers': speakers}

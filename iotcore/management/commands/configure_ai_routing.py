from django.core.management.base import BaseCommand, CommandError
from iotcore.models import AIServiceRoute, Device
from iotcore.ai.routing import worker_url, SERVICES


class Command(BaseCommand):
    help = 'Initialize per-model routing without overwriting saved user choices.'

    def add_arguments(self, parser):
        parser.add_argument('--active', default='home-ai-sub1')
        parser.add_argument('--standby', default='home-ai-main')

    def handle(self, *args, **options):
        active = Device.objects.filter(device_uid=options['active']).first()
        standby = Device.objects.filter(device_uid=options['standby']).first()
        if active is None or standby is None or active.pk == standby.pk:
            raise CommandError('Distinct active and standby PC devices are required.')
        for device in [active, standby]:
            try:
                worker_url('asr', device)
            except (ValueError, TypeError, AttributeError) as exc:
                raise CommandError(str(exc))
        for service in SERVICES:
            route, created = AIServiceRoute.objects.get_or_create(service=service, defaults={
                'active_device': active, 'standby_device': standby, 'configured': True})
            self.stdout.write(f'{service}: ' + ('initialized' if created else 'existing selection kept'))

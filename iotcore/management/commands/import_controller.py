# smartcore/management/commands/import_controllers.py
from django.core.management.base import BaseCommand
from smartcore.models import Controller
import csv

class Command(BaseCommand):
    help = 'Import controllers from CSV'

    def handle(self, *args, **kwargs):
        with open('E:/Python/github/PLAYGROUND/smartcore/management/data/Controller.csv', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                Controller.objects.create(
                    name=row['name'],
                    mac_address=row['mac_address'],
                    ip_address=row['ip_address'],
                    location=row['location']
                )
            self.stdout.write(self.style.SUCCESS('Controllers imported successfully.'))

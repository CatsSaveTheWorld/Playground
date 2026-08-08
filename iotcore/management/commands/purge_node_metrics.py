from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from ...models import NodeMetricSample


class Command(BaseCommand):
    help = "Delete raw node telemetry samples older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=int(getattr(settings, "NODE_METRIC_RETENTION_HOURS", 24)),
        )

    def handle(self, *args, **options):
        hours = max(1, int(options["hours"]))
        cutoff = timezone.now() - timedelta(hours=hours)
        queryset = NodeMetricSample.objects.filter(recorded_at__lt=cutoff)
        count = queryset.count()
        queryset.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"{count}개 시스템 telemetry 샘플 삭제 (보존 {hours}시간)"
            )
        )

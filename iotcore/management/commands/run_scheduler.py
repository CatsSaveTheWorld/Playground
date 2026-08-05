import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ...scheduler.service import AutomationService


class Command(BaseCommand):
    help = "Enqueue due time-based IoTCore automation triggers."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=5)

    def handle(self, *args, **options):
        while True:
            try:
                close_old_connections()
                runs = AutomationService.enqueue_due()
                if runs:
                    self.stdout.write(f"{len(runs)}개 실행 요청을 등록했습니다.")
            except Exception as exc:
                self.stderr.write(
                    f"시간 자동화 처리 실패: {type(exc).__name__}: {exc}"
                )
                if options["once"]:
                    raise
            finally:
                close_old_connections()

            if options["once"]:
                return
            time.sleep(max(options["poll_interval"], 0.2))

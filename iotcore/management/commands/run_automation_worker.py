import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ...scheduler.executor import AutomationExecutor


class Command(BaseCommand):
    help = "Execute pending IoTCore Automation runs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=0.2)

    def handle(self, *args, **options):
        while True:
            processed = None
            try:
                close_old_connections()
                processed = AutomationExecutor.run_next_pending()
                if processed is not None:
                    self.stdout.write(
                        f"자동화 실행 #{processed.pk}: "
                        f"{processed.get_status_display()}"
                    )
            except Exception as exc:
                self.stderr.write(
                    f"자동화 워커 처리 실패: {type(exc).__name__}: {exc}"
                )
                if options["once"]:
                    raise
            finally:
                close_old_connections()

            if options["once"]:
                return
            if processed is None:
                time.sleep(
                    AutomationExecutor.next_poll_delay(
                        options["poll_interval"]
                    )
                )

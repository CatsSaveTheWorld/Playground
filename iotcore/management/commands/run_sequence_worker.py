import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ...device.services.sequence_executor import SequenceExecutor
from ...scheduler.executor import AutomationExecutor


class Command(BaseCommand):
    help = "Execute pending IoTCore automation and sequence runs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=1)

    def handle(self, *args, **options):
        while True:
            processed = None
            try:
                close_old_connections()
                processed = AutomationExecutor.run_next_pending()
                if processed is not None:
                    self.stdout.write(
                        f"예약 실행 #{processed.pk}: "
                        f"{processed.get_status_display()}"
                    )
                else:
                    processed = SequenceExecutor.run_next_pending()
                    if processed is not None:
                        self.stdout.write(
                            f"시퀀스 실행 #{processed.pk}: "
                            f"{processed.get_status_display()}"
                        )
            except Exception as exc:
                self.stderr.write(
                    f"실행 워커 처리 실패: {type(exc).__name__}: {exc}"
                )
                if options["once"]:
                    raise
            finally:
                close_old_connections()

            if options["once"]:
                return
            if processed is None:
                time.sleep(max(options["poll_interval"], 0.2))

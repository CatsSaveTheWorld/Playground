from ...models import SequenceStep

class SequenceService:

    @staticmethod
    def normalize_order(sequence):
        steps = (
            sequence.steps
            .order_by("order", "id")
        )

        for index, step in enumerate(steps, start=1):
            step.order = index

        SequenceStep.objects.bulk_update(
            steps,
            ["order"]
        )
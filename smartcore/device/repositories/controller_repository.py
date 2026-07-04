from ...models import Controller


class ControllerRepository:

    @staticmethod
    def get_controller(controller_id):
        try:
            return Controller.objects.get(id=controller_id)
        except Controller.DoesNotExist:
            return None

    @staticmethod
    def get_with_device(controller_id):
        try:
            return (
                Controller.objects
                .select_related("device")
                .get(id=controller_id)
            )
        except Controller.DoesNotExist:
            return None


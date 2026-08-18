from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0020_trigger_condition_sets"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="automationaction",
            name="unique_automation_action_order",
        ),
        migrations.AlterField(
            model_name="automationaction",
            name="trigger",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="actions",
                to="iotcore.automationtrigger",
            ),
        ),
        migrations.AlterModelOptions(
            name="automationaction",
            options={"ordering": ["trigger_id", "order", "id"]},
        ),
        migrations.AddConstraint(
            model_name="automationaction",
            constraint=models.UniqueConstraint(
                fields=("trigger", "order"),
                name="unique_automation_trigger_action_order",
            ),
        ),
    ]

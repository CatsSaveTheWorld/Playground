from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0008_automation_actions_and_runs"),
    ]

    operations = [
        migrations.AlterField(
            model_name="devicestate",
            name="key",
            field=models.CharField(max_length=255),
        ),
    ]

# Generated for non-blocking Automation Action scheduling.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0028_initialize_device_state_rows"),
    ]

    operations = [
        migrations.AddField(
            model_name="actionrun",
            name="scheduled_for",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]

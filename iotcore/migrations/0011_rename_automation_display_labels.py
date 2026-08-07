from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0010_sequencerun_detach_on_sequence_delete"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="automation",
            options={
                "ordering": ["name"],
                "verbose_name": "예약 실행",
                "verbose_name_plural": "예약 실행",
            },
        ),
        migrations.AlterField(
            model_name="sequencerun",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("manual", "수동"),
                    ("automation", "예약 실행"),
                ],
                max_length=20,
            ),
        ),
    ]

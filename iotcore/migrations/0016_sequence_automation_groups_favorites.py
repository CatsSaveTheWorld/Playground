import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0015_device_role_sensor_support"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutomationGroup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("order", models.PositiveIntegerField(db_index=True, default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "예약 실행 그룹",
                "verbose_name_plural": "예약 실행 그룹",
                "ordering": ["order", "name", "id"],
            },
        ),
        migrations.CreateModel(
            name="SequenceGroup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
                ("order", models.PositiveIntegerField(db_index=True, default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "시퀀스 그룹",
                "verbose_name_plural": "시퀀스 그룹",
                "ordering": ["order", "name", "id"],
            },
        ),
        migrations.AddField(
            model_name="automation",
            name="group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="automations",
                to="iotcore.automationgroup",
            ),
        ),
        migrations.AddField(
            model_name="automation",
            name="is_favorite",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="sequence",
            name="group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sequences",
                to="iotcore.sequencegroup",
            ),
        ),
        migrations.AddField(
            model_name="sequence",
            name="is_favorite",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]

from django.db import migrations, models


LEGACY_ALIASES = {
    "에어컨 송풍 종료": ["송풍 종료", "에어컨 송풍 끝"],
    "영화 모드": ["영화 세팅", "영화 설정", "영화 볼 준비"],
    "게임 모드": ["게임 세팅", "게임 설정", "게임 준비"],
    "취침 모드": ["취침 세팅", "잘 준비", "잘게"],
    "전등 켜기": ["전등 켜", "불 켜", "조명 켜"],
    "전등 끄기": ["전등 꺼", "불 꺼", "조명 꺼"],
    "창문 열기": ["창문 열어", "환기해"],
    "창문 닫기": ["창문 닫아"],
}


def seed_legacy_aliases(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")
    for name, aliases in LEGACY_ALIASES.items():
        Automation.objects.filter(name=name).update(voice_aliases=aliases)


def clear_legacy_aliases(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")
    Automation.objects.filter(name__in=LEGACY_ALIASES).update(voice_aliases=[])


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0032_initialize_window_pusher_states"),
    ]

    operations = [
        migrations.AddField(
            model_name="automation",
            name="voice_aliases",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(seed_legacy_aliases, clear_legacy_aliases),
    ]

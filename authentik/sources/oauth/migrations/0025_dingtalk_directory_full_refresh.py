from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0024_merge_2026_8_0"),
    ]

    operations = [
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="last_full_success_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0025_dingtalk_directory_full_refresh"),
    ]

    operations = [
        migrations.CreateModel(
            name="DingTalkApiUsageBucket",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("hour_start", models.DateTimeField()),
                ("category", models.TextField()),
                ("count", models.PositiveBigIntegerField(default=0)),
                ("blocked_count", models.PositiveBigIntegerField(default=0)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="authentik_sources_oauth.oauthsource",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["source", "hour_start"], name="ak_dt_usage_src_hour_idx"),
                    models.Index(fields=["hour_start"], name="ak_dt_usage_hour_idx"),
                ],
                "unique_together": {("source", "hour_start", "category")},
            },
        ),
    ]

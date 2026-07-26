from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0021_dingtalk_directory_sync_staging"),
    ]

    operations = [
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="error_code",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="error_params",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="error_correlation_id",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]

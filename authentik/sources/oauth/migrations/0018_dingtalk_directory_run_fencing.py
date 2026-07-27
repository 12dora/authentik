from django.db import migrations, models


def scrub_dingtalk_sync_errors(apps, schema_editor):
    status_model = apps.get_model("authentik_sources_oauth", "DingTalkDirectorySyncStatus")
    sensitive_markers = (
        "access_token",
        "appsecret",
        "consumer_secret",
        "x-acs-dingtalk-access-token",
    )
    for status in status_model.objects.exclude(error="").iterator():
        if any(marker in status.error.lower() for marker in sensitive_markers):
            status.error = "DingTalk directory sync failed; sensitive error detail was removed."
            status.save(update_fields=["error"])


def initialize_dingtalk_run_sequences(apps, schema_editor):
    status_model = apps.get_model("authentik_sources_oauth", "DingTalkDirectorySyncStatus")
    for status in status_model.objects.only("pk", "generation").iterator():
        status.run_sequence = status.generation
        status.save(update_fields=["run_sequence"])


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0017_dingtalk_directory_sync_generation"),
    ]

    operations = [
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="active_run_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="run_sequence",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(initialize_dingtalk_run_sequences, migrations.RunPython.noop),
        migrations.RunPython(scrub_dingtalk_sync_errors, migrations.RunPython.noop),
    ]

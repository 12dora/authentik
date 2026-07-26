from django.db import migrations, models


def backfill_dingtalk_success_timestamps(apps, schema_editor):
    status_model = apps.get_model("authentik_sources_oauth", "DingTalkDirectorySyncStatus")
    for status in status_model.objects.only("pk", "status", "finished_at").iterator():
        update_fields = []
        if status.finished_at:
            status.last_attempt_at = status.finished_at
            update_fields.append("last_attempt_at")
        if status.status == "success" and status.finished_at:
            status.last_success_at = status.finished_at
            update_fields.append("last_success_at")
        if update_fields:
            status.save(update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0018_dingtalk_directory_run_fencing"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dingtalkdirectorysyncstatus",
            name="status",
            field=models.TextField(
                choices=[
                    ("unknown", "Unknown"),
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("error", "Error"),
                    ("deleted", "Deleted"),
                ],
                default="unknown",
            ),
        ),
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="last_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dingtalkdirectorysyncstatus",
            name="last_success_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_dingtalk_success_timestamps, migrations.RunPython.noop),
    ]

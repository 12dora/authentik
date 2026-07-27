from uuid import NAMESPACE_URL, uuid5

from django.db import migrations, models

DINGTALK_DIRECTORY_LEGACY_ERROR_CODE = "dingtalk_directory_sync_failed"
DINGTALK_DIRECTORY_STABLE_ERROR_CODES = frozenset(
    {
        "dingtalk_directory_broker_unavailable",
        "dingtalk_directory_concurrency_limit",
        "dingtalk_directory_http_request_failed",
        "dingtalk_directory_invalid_response",
        "dingtalk_directory_payload_limit",
        "dingtalk_directory_run_stale",
        "dingtalk_directory_source_disabled",
        "dingtalk_directory_source_unavailable",
        "dingtalk_directory_unsupported_source",
        "dingtalk_directory_user_limit",
        "dingtalk_directory_user_detail_failed",
        DINGTALK_DIRECTORY_LEGACY_ERROR_CODE,
    }
)


def migrate_legacy_dingtalk_directory_errors(apps, schema_editor):
    status_model = apps.get_model("authentik_sources_oauth", "DingTalkDirectorySyncStatus")
    db_alias = schema_editor.connection.alias
    for status in (
        status_model.objects.using(db_alias)
        .filter(status="error")
        .exclude(error="")
        .order_by("pk")
    ):
        correlation_id = status.error_correlation_id or uuid5(
            NAMESPACE_URL,
            f"authentik:dingtalk-directory-sync:legacy-error:{status.pk}",
        )
        is_legacy_raw = status.error not in DINGTALK_DIRECTORY_STABLE_ERROR_CODES
        error_code = status.error if not is_legacy_raw else DINGTALK_DIRECTORY_LEGACY_ERROR_CODE
        status.error = error_code
        status.error_code = error_code
        status.error_params = {"legacy_error": "redacted"} if is_legacy_raw else {}
        status.error_correlation_id = correlation_id
        status.save(
            update_fields=[
                "error",
                "error_code",
                "error_params",
                "error_correlation_id",
            ]
        )


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
        migrations.RunPython(
            migrate_legacy_dingtalk_directory_errors,
            migrations.RunPython.noop,
        ),
    ]

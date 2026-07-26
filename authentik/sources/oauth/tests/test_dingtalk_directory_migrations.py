"""DingTalk directory migration tests."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from authentik.sources.oauth.dingtalk.sync import (
    DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED,
    DINGTALK_SYNC_ERROR_UNKNOWN,
)
from authentik.sources.oauth.models import OAuthSource


class TestDingTalkDirectorySyncErrorMigration(TransactionTestCase):
    migrate_from = [("authentik_sources_oauth", "0021_dingtalk_directory_sync_staging")]
    migrate_to = [("authentik_sources_oauth", "0022_dingtalk_directory_sync_error_contract")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.source_pk = self._create_source()
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        self._create_legacy_error_status(old_apps)
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        self.apps = self.executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def _create_source(self):
        return OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        ).pk

    def _create_legacy_error_status(self, apps):
        status_model = apps.get_model("authentik_sources_oauth", "DingTalkDirectorySyncStatus")
        status_model.objects.create(
            source_id=self.source_pk,
            corp_id="CORP",
            status="error",
            error="provider failed with access_token=SECRET_TOKEN",
        )
        status_model.objects.create(
            source_id=self.source_pk,
            corp_id="PREF",
            status="error",
            error=f"{DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED} access_token=SECRET_TOKEN",
        )
        status_model.objects.create(
            source_id=self.source_pk,
            corp_id="STABLE",
            status="error",
            error=DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED,
        )

    def test_legacy_error_text_is_replaced_with_public_code(self):
        status_model = self.apps.get_model(
            "authentik_sources_oauth", "DingTalkDirectorySyncStatus"
        )

        raw = status_model.objects.get(source_id=self.source_pk, corp_id="CORP")
        prefixed = status_model.objects.get(source_id=self.source_pk, corp_id="PREF")
        stable = status_model.objects.get(source_id=self.source_pk, corp_id="STABLE")

        for status in (raw, prefixed):
            self.assertEqual(status.error, DINGTALK_SYNC_ERROR_UNKNOWN)
            self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_UNKNOWN)
            self.assertEqual(status.error_params, {"legacy_error": "redacted"})
            self.assertIsNotNone(status.error_correlation_id)
        self.assertEqual(stable.error, DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED)
        self.assertEqual(stable.error_code, DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED)
        self.assertEqual(stable.error_params, {})
        self.assertIsNotNone(stable.error_correlation_id)

    def test_reverse_migration_does_not_restore_raw_error_text(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        status_model = old_apps.get_model(
            "authentik_sources_oauth", "DingTalkDirectorySyncStatus"
        )

        for corp_id in ("CORP", "PREF"):
            status = status_model.objects.get(source_id=self.source_pk, corp_id=corp_id)
            self.assertEqual(status.error, DINGTALK_SYNC_ERROR_UNKNOWN)
            self.assertNotIn("SECRET_TOKEN", status.error)
            self.assertNotIn("access_token", status.error)

"""DingTalk directory API tests."""

from unittest.mock import patch

from django.urls import reverse
from django.utils.timezone import now
from rest_framework.test import APIClient, APITestCase

from authentik.core.tests.utils import create_test_admin_user, create_test_user
from authentik.sources.oauth.dingtalk.sync import (
    DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
    DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED,
    DINGTALK_SYNC_ERROR_UNKNOWN,
)
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectorySyncStatusChoices,
    DingTalkDirectoryUser,
    OAuthSource,
    UserOAuthSourceConnection,
)


class TestDingTalkDirectoryAPI(APITestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            mobile="13800000000",
            email="ada@example.invalid",
            job_number="E-001",
            union_id="UNION",
            open_id="OPEN",
            dept_id_list=["1"],
            raw={"userid": "USER", "sensitive": "value"},
            last_seen_at=now(),
        )

    def authenticate(self, user):
        self.client.force_login(user)
        self.client.force_authenticate(user=user)

    def test_user_list_requires_directory_permissions(self):
        self.authenticate(create_test_user("regular"))
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )
        self.assertEqual(response.status_code, 403)

        source_reader = create_test_user("source-reader")
        source_reader.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource")
        self.authenticate(source_reader)
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )
        self.assertEqual(response.status_code, 403)

    def test_user_list_allows_directory_user_reader(self):
        directory_reader = create_test_user("directory-reader")
        directory_reader.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource")
        directory_reader.assign_perms_to_managed_role(
            "authentik_sources_oauth.view_dingtalkdirectoryuser"
        )
        self.authenticate(directory_reader)

        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )

        self.assertEqual(response.status_code, 200)

    def test_status_includes_snapshot_generation(self):
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            generation=9,
            finished_at=now(),
            counters={"users": 1, "departments": 0},
        )
        self.authenticate(create_test_admin_user())

        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-status", kwargs={"source_slug": "dingtalk"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sync"][0]["generation"], 9)

    def test_status_hides_legacy_raw_error_text(self):
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status=DingTalkDirectorySyncStatusChoices.ERROR,
            error=f"{DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED} access_token=SECRET_TOKEN",
            finished_at=now(),
        )
        self.authenticate(create_test_admin_user())

        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-status", kwargs={"source_slug": "dingtalk"})
        )

        self.assertEqual(response.status_code, 200)
        sync = response.json()["sync"][0]
        self.assertEqual(sync["error"], DINGTALK_SYNC_ERROR_UNKNOWN)
        self.assertEqual(sync["error_code"], DINGTALK_SYNC_ERROR_UNKNOWN)
        self.assertEqual(sync["error_params"], {"legacy_error": "redacted"})
        self.assertNotIn("SECRET_TOKEN", response.content.decode())
        self.assertNotIn("access_token", response.content.decode())

    def test_status_reports_view_only_user_cannot_change(self):
        user = create_test_user("directory-status-viewer")
        user.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource", self.source)
        self.authenticate(user)

        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-status", kwargs={"source_slug": "dingtalk"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["can_change"])

    def test_status_reports_object_change_user_can_change(self):
        user = create_test_user("directory-status-object-changer")
        user.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource", self.source)
        user.assign_perms_to_managed_role("authentik_sources_oauth.change_oauthsource", self.source)
        self.authenticate(user)

        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-status", kwargs={"source_slug": "dingtalk"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["can_change"])

    def test_status_reports_model_change_user_can_change(self):
        user = create_test_user("directory-status-model-changer")
        user.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource", self.source)
        user.assign_perms_to_managed_role("authentik_sources_oauth.change_oauthsource")
        self.authenticate(user)

        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-status", kwargs={"source_slug": "dingtalk"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["can_change"])

    def test_user_list_returns_contact_fields_without_private_identifiers(self):
        self.authenticate(create_test_admin_user())
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertEqual(item["user_id"], "USER")
        self.assertEqual(item["email"], "ada@example.invalid")
        self.assertEqual(item["mobile"], "13800000000")
        self.assertEqual(item["job_number"], "E-001")
        self.assertNotIn("raw", item)
        self.assertNotIn("union_id", item)
        self.assertNotIn("open_id", item)

    def test_user_list_excludes_deleted_cache_entries(self):
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="DELETED",
            name="Deleted",
            is_deleted=True,
            last_seen_at=now(),
        )

        self.authenticate(create_test_admin_user())
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["user_id"] for item in response.json()["results"]], ["USER"])

    def test_department_list_excludes_deleted_cache_entries(self):
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="1",
            name="Active",
            last_seen_at=now(),
        )
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="2",
            name="Deleted",
            is_deleted=True,
            last_seen_at=now(),
        )

        self.authenticate(create_test_admin_user())
        response = self.client.get(
            reverse(
                "authentik_api:dingtalk-directory-departments",
                kwargs={"source_slug": "dingtalk"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["dept_id"] for item in response.json()["results"]], ["1"])

    @patch("authentik.sources.oauth.api.dingtalk_directory.dingtalk_directory_sync.send")
    def test_sync_post_creates_durable_queued_status(self, send_mock):
        self.authenticate(create_test_admin_user())
        response = self.client.post(
            reverse("authentik_api:dingtalk-directory-sync", kwargs={"source_slug": "dingtalk"}),
            data={"corp_id": "CORP"},
            content_type="application/json",
        )
        duplicate = self.client.post(
            reverse("authentik_api:dingtalk-directory-sync", kwargs={"source_slug": "dingtalk"}),
            data={"corp_id": "CORP"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["queued"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["queued"])
        self.assertEqual(send_mock.call_count, 1)
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.QUEUED)
        self.assertIsNotNone(status.active_run_id)

    @patch("authentik.sources.oauth.api.dingtalk_directory.dingtalk_directory_sync.send")
    def test_sync_post_marks_error_when_broker_rejects(self, send_mock):
        send_mock.side_effect = RuntimeError("broker unavailable")
        self.authenticate(create_test_admin_user())

        response = self.client.post(
            reverse("authentik_api:dingtalk-directory-sync", kwargs={"source_slug": "dingtalk"}),
            data={"corp_id": "CORP"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertIsNone(status.active_run_id)
        self.assertEqual(status.error, DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE)
        self.assertIsNotNone(status.error_correlation_id)

    def test_sync_delete_clears_corp_cache_and_marks_status_deleted(self):
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status=DingTalkDirectorySyncStatusChoices.SUCCESS,
            finished_at=now(),
        )
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="1",
            name="Active",
            last_seen_at=now(),
        )

        self.authenticate(create_test_admin_user())
        response = self.client.delete(
            reverse("authentik_api:dingtalk-directory-sync", kwargs={"source_slug": "dingtalk"}),
            data={"corp_id": "CORP"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": True, "corp_id": "CORP"})
        status = DingTalkDirectorySyncStatus.objects.get(corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.DELETED)
        self.assertIsNone(status.active_run_id)
        self.assertFalse(DingTalkDirectoryDepartment.objects.filter(corp_id="CORP").exists())
        self.assertFalse(DingTalkDirectoryUser.objects.filter(corp_id="CORP").exists())

    def test_org_context_self_access_uses_source_scoped_identity(self):
        other_source = OAuthSource.objects.create(
            name="Other DingTalk",
            slug="dingtalk-other",
            provider_type="dingtalk",
            consumer_key="OTHER_CLIENT_ID",
            consumer_secret="OTHER_CLIENT_SECRET",
        )
        seen = now()
        for source, corp_id, user_id, union_id in (
            (self.source, "CORP_A", "USER_A", "UNION_A"),
            (other_source, "CORP_B", "USER_B", "UNION_B"),
        ):
            DingTalkDirectorySyncStatus.objects.create(
                source=source,
                corp_id=corp_id,
                status="success",
                finished_at=seen,
                last_success_at=seen,
            )
            DingTalkDirectoryUser.objects.create(
                source=source,
                corp_id=corp_id,
                user_id=user_id,
                union_id=union_id,
                name=user_id,
                dept_id_list=[],
                last_seen_at=seen,
            )
        user = create_test_user("source-scoped")
        user.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource")
        user.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource", self.source)
        user.attributes = {
            "dingtalk": {"corp_id": "CORP_B", "user_id": "USER_B"},
            "dingtalk_sources": {
                str(self.source.pk): {
                    "source_pk": str(self.source.pk),
                    "source_slug": self.source.slug,
                    "corp_id": "CORP_A",
                    "user_id": "USER_A",
                },
                str(other_source.pk): {
                    "source_pk": str(other_source.pk),
                    "source_slug": other_source.slug,
                    "corp_id": "CORP_B",
                    "user_id": "USER_B",
                },
            },
        }
        user.save()
        UserOAuthSourceConnection.objects.create(
            user=user,
            source=self.source,
            identifier="UNION_A",
        )
        client = APIClient()
        client.force_authenticate(user=user)

        own_response = client.get(
            reverse(
                "authentik_api:dingtalk-directory-user-org",
                kwargs={
                    "source_slug": "dingtalk",
                    "corp_id": "CORP_A",
                    "user_id": "USER_A",
                },
            )
        )
        other_response = client.get(
            reverse(
                "authentik_api:dingtalk-directory-user-org",
                kwargs={
                    "source_slug": "dingtalk",
                    "corp_id": "CORP_B",
                    "user_id": "USER_B",
                },
            )
        )

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response.json()["corp_id"], "CORP_A")
        self.assertEqual(other_response.status_code, 403)

    def test_org_context_directory_reader_can_load_other_users(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            last_success_at=seen,
        )
        self.authenticate(create_test_admin_user())

        response = self.client.get(
            reverse(
                "authentik_api:dingtalk-directory-user-org",
                kwargs={
                    "source_slug": "dingtalk",
                    "corp_id": "CORP",
                    "user_id": "USER",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["corp_id"], "CORP")
        self.assertEqual(response.json()["user_id"], "USER")

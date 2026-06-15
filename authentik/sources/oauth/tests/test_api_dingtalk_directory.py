"""DingTalk directory API tests."""

from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from authentik.core.tests.utils import create_test_admin_user, create_test_user
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)


class TestDingTalkDirectoryAPI(TestCase):
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
            dept_id_list=["1"],
            last_seen_at=now(),
        )

    def test_user_list_requires_admin_permission(self):
        self.client.force_login(create_test_user("regular"))
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )
        self.assertEqual(response.status_code, 403)

    def test_user_list_hides_sensitive_fields(self):
        self.client.force_login(create_test_admin_user())
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertEqual(item["user_id"], "USER")
        self.assertNotIn("mobile", item)
        self.assertNotIn("email", item)
        self.assertNotIn("raw", item)

    def test_user_list_excludes_deleted_cache_entries(self):
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="DELETED",
            name="Deleted",
            is_deleted=True,
            last_seen_at=now(),
        )

        self.client.force_login(create_test_admin_user())
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

        self.client.force_login(create_test_admin_user())
        response = self.client.get(
            reverse(
                "authentik_api:dingtalk-directory-departments",
                kwargs={"source_slug": "dingtalk"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["dept_id"] for item in response.json()["results"]], ["1"])

    def test_sync_delete_clears_corp_cache_and_status(self):
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=now(),
        )
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="1",
            name="Active",
            last_seen_at=now(),
        )

        self.client.force_login(create_test_admin_user())
        response = self.client.delete(
            reverse("authentik_api:dingtalk-directory-sync", kwargs={"source_slug": "dingtalk"}),
            data={"corp_id": "CORP"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": True, "corp_id": "CORP"})
        self.assertFalse(DingTalkDirectorySyncStatus.objects.filter(corp_id="CORP").exists())
        self.assertFalse(DingTalkDirectoryDepartment.objects.filter(corp_id="CORP").exists())
        self.assertFalse(DingTalkDirectoryUser.objects.filter(corp_id="CORP").exists())

"""DingTalk directory model, sync, and selector tests."""

from unittest.mock import patch

from django.test import TestCase
from django.utils.timezone import now

from authentik.core.tests.utils import create_test_user
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context
from authentik.sources.oauth.dingtalk.sync import sync_dingtalk_directory
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)


class TestDingTalkDirectoryModels(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def test_user_and_department_are_unique_per_source_and_corp(self):
        seen = now()
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="1",
            name="HQ",
            parent_dept_id="",
            last_seen_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            manager_user_id="MANAGER",
            dept_id_list=["1"],
            last_seen_at=seen,
        )
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            counters={"users": 1, "departments": 1},
        )

        self.assertEqual(DingTalkDirectoryDepartment.objects.count(), 1)
        self.assertEqual(DingTalkDirectoryUser.objects.get().manager_user_id, "MANAGER")
        self.assertEqual(DingTalkDirectorySyncStatus.objects.get().counters["users"], 1)


class TestDingTalkDirectorySync(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_sync_upserts_departments_and_users(self, client_cls):
        client = client_cls.return_value
        client.iter_departments.return_value = [
            {"dept_id": "2", "name": "Engineering", "parent_dept_id": "1", "raw": {"dept_id": 2}},
        ]
        client.iter_department_users.side_effect = [
            [
                {
                    "userid": "ROOT_USER",
                    "name": "Root Ada",
                    "dept_id_list": [1],
                    "active": True,
                }
            ],
            [
                {
                    "userid": "USER",
                    "unionid": "UNION",
                    "name": "Ada",
                    "title": "Engineer",
                    "manager_userid": "MANAGER",
                    "dept_id_list": [2],
                    "active": True,
                }
            ],
        ]

        result = sync_dingtalk_directory(self.source, corp_id="CORP")

        self.assertEqual(result["departments"], 2)
        self.assertEqual(result["users"], 2)
        self.assertTrue(DingTalkDirectoryDepartment.objects.filter(dept_id="1").exists())
        self.assertTrue(DingTalkDirectoryUser.objects.filter(user_id="ROOT_USER").exists())
        user = DingTalkDirectoryUser.objects.get(user_id="USER")
        self.assertEqual(user.user_id, "USER")
        self.assertEqual(user.manager_user_id, "MANAGER")
        self.assertEqual(user.dept_id_list, ["2"])
        self.assertEqual(DingTalkDirectorySyncStatus.objects.get().status, "success")

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_sync_error_status_survives_raised_client_error(self, client_cls):
        client_cls.return_value.iter_departments.side_effect = ValueError("missing permission")

        with self.assertRaisesMessage(ValueError, "missing permission"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, "error")
        self.assertEqual(status.error, "missing permission")

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_incomplete_sync_does_not_soft_delete_previously_cached_users(self, client_cls):
        """C2: an empty-but-"successful" sync must not wipe a populated cache."""
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source, corp_id="CORP", status="success", finished_at=seen
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            union_id="UNION",
            name="Ada",
            dept_id_list=["1"],
            last_seen_at=seen,
        )
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.return_value = []

        result = sync_dingtalk_directory(self.source, corp_id="CORP")

        cached = DingTalkDirectoryUser.objects.get(source=self.source, corp_id="CORP", user_id="USER")
        self.assertFalse(cached.is_deleted)
        self.assertTrue(
            any("Skipped user deletion" in warning for warning in result.get("warnings", []))
        )


class TestDingTalkOrgContext(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def test_org_context_returns_department_path_and_manager_chain(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
        )
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="1",
            name="HQ",
            parent_dept_id="",
            last_seen_at=seen,
        )
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="2",
            name="Engineering",
            parent_dept_id="1",
            last_seen_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="MANAGER",
            name="Grace",
            title="Director",
            dept_id_list=["1"],
            last_seen_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            title="Engineer",
            manager_user_id="MANAGER",
            dept_id_list=["2"],
            last_seen_at=seen,
        )
        user = create_test_user("ada")
        user.attributes = {"dingtalk": {"corp_id": "CORP", "user_id": "USER"}}
        user.save()

        context = get_dingtalk_org_context(user, source_slug="dingtalk")

        self.assertEqual(context["departments"][0]["path"][0]["name"], "HQ")
        self.assertEqual(context["departments"][0]["path"][1]["name"], "Engineering")
        self.assertEqual(context["manager"]["user_id"], "MANAGER")
        self.assertEqual(context["manager_chain"][0]["name"], "Grace")
        self.assertFalse(context["stale"])

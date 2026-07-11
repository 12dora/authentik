"""DingTalk directory model, sync, and selector tests."""

from unittest.mock import patch

from django.test import TestCase
from django.utils.timezone import now
from requests import HTTPError, Response

from authentik.core.tests.utils import create_test_user
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context
from authentik.sources.oauth.dingtalk.sync import (
    _publish_snapshot,
    _start_sync_run,
    safe_dingtalk_sync_error,
    sync_dingtalk_directory,
)
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
        client.get_user_detail.return_value = {}
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
        status = DingTalkDirectorySyncStatus.objects.get()
        self.assertEqual(status.status, "success")
        self.assertEqual(status.generation, 1)

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_sync_enriches_manager_from_user_detail(self, client_cls):
        """v2/user/list never returns manager_userid; it must be enriched via v2/user/get."""
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.side_effect = [
            [
                {
                    "userid": "USER",
                    "unionid": "UNION",
                    "name": "Ada",
                    "dept_id_list": [1],
                    "active": True,
                }
            ],
        ]
        client.get_user_detail.return_value = {"manager_userid": "BOSS"}

        _ = sync_dingtalk_directory(self.source, corp_id="CORP")

        user = DingTalkDirectoryUser.objects.get(user_id="USER")
        self.assertEqual(user.manager_user_id, "BOSS")

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_sync_error_status_survives_raised_client_error(self, client_cls):
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            generation=7,
            finished_at=now(),
        )
        client_cls.return_value.iter_departments.side_effect = ValueError("missing permission")

        with self.assertRaisesMessage(ValueError, "missing permission"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, "error")
        self.assertEqual(status.error, "missing permission")
        self.assertEqual(status.generation, 7)

    def test_out_of_order_run_cannot_publish_over_newer_snapshot(self):
        first_started = now()
        first_run_id, first_sequence = _start_sync_run(self.source, "CORP", first_started)
        second_started = now()
        second_run_id, second_sequence = _start_sync_run(self.source, "CORP", second_started)

        second = _publish_snapshot(
            self.source,
            "CORP",
            [{"dept_id": "2", "name": "New", "parent_dept_id": "1", "raw": {}}],
            {},
            second_started,
            [],
            (second_run_id, second_sequence),
        )
        stale = _publish_snapshot(
            self.source,
            "CORP",
            [{"dept_id": "3", "name": "Old", "parent_dept_id": "1", "raw": {}}],
            {},
            first_started,
            [],
            (first_run_id, first_sequence),
        )

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(second["departments"], 1)
        self.assertTrue(stale["stale"])
        self.assertEqual(status.generation, second_sequence)
        self.assertTrue(
            DingTalkDirectoryDepartment.objects.filter(
                source=self.source, corp_id="CORP", dept_id="2", is_deleted=False
            ).exists()
        )
        self.assertFalse(
            DingTalkDirectoryDepartment.objects.filter(
                source=self.source, corp_id="CORP", dept_id="3"
            ).exists()
        )

    def test_http_error_status_never_persists_request_url_or_token(self):
        response = Response()
        response.status_code = 403
        response.url = "https://oapi.dingtalk.com/path?access_token=SECRET_TOKEN"
        error = HTTPError(f"403 for url: {response.url}", response=response)

        detail = safe_dingtalk_sync_error(error)

        self.assertEqual(detail, "DingTalk HTTP request failed (status 403).")
        self.assertNotIn("SECRET_TOKEN", detail)
        self.assertNotIn("access_token", detail)

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_empty_sync_soft_deletes_previously_cached_entries(self, client_cls):
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
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="2",
            name="Old department",
            parent_dept_id="1",
            last_seen_at=seen,
        )
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.return_value = []

        result = sync_dingtalk_directory(self.source, corp_id="CORP")

        cached = DingTalkDirectoryUser.objects.get(
            source=self.source, corp_id="CORP", user_id="USER"
        )
        old_department = DingTalkDirectoryDepartment.objects.get(
            source=self.source, corp_id="CORP", dept_id="2"
        )
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertTrue(cached.is_deleted)
        self.assertTrue(old_department.is_deleted)
        self.assertEqual(result["users"], 0)
        self.assertEqual(result["departments"], 1)
        self.assertEqual(status.counters["users"], 0)
        self.assertEqual(status.counters["departments"], 1)
        self.assertEqual(status.generation, 1)

        sync_dingtalk_directory(self.source, corp_id="CORP")

        status.refresh_from_db()
        self.assertEqual(status.generation, 2)


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

"""DingTalk directory model, sync, and selector tests."""

from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from requests import HTTPError, Response
from requests.exceptions import RequestException
from structlog.testing import capture_logs

from authentik.core.tests.utils import create_test_user
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context
from authentik.sources.oauth.dingtalk.sync import (
    DINGTALK_SYNC_ERROR_APP_TOKEN_FAILED,
    DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
    DINGTALK_SYNC_ERROR_CORP_MISMATCH,
    DINGTALK_SYNC_ERROR_CORP_UNAUTHORIZED,
    DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED,
    DINGTALK_SYNC_ERROR_INVALID_RESPONSE,
    DINGTALK_SYNC_ERROR_SOURCE_DISABLED,
    _publish_snapshot,
    _start_sync_run,
    classify_dingtalk_sync_error,
    finalize_dingtalk_directory_sync_error,
    queue_dingtalk_directory_sync,
    safe_dingtalk_sync_error,
    sync_dingtalk_directory,
)
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectoryDepartmentStage,
    DingTalkDirectorySyncStatus,
    DingTalkDirectorySyncStatusChoices,
    DingTalkDirectoryUser,
    DingTalkDirectoryUserStage,
    OAuthSource,
    UserOAuthSourceConnection,
)
from authentik.sources.oauth.tasks import dingtalk_directory_sync as dingtalk_directory_sync_task
from authentik.sources.oauth.tasks import dingtalk_directory_sync_all
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_CORP_ID_ECHO_KEYS,
    DingTalkAppTokenError,
    DingTalkDepartmentCorpUnavailable,
    DingTalkDepartmentLoadFailed,
    extract_dingtalk_corp_ids,
)

# Mirrors what /v1.0/contact/organizations/authInfos actually returns: the corp identity
# is nested under an envelope and spelled `corpid`, not `result.corpId`.
ORG_AUTH_CORP = {
    "raw": {"auth_org_info": {"corpid": "CORP", "corp_name": "Example"}},
    "label": "Example",
}


class TestDingTalkDirectoryModels(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        self.org_auth_patcher = patch(
            "authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info",
            return_value=ORG_AUTH_CORP,
        )
        self.org_auth_patcher.start()
        self.addCleanup(self.org_auth_patcher.stop)

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
        self.org_auth_patcher = patch(
            "authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info",
            return_value=ORG_AUTH_CORP,
        )
        self.org_auth_patcher.start()
        self.addCleanup(self.org_auth_patcher.stop)

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
                    "email": "ada@example.invalid",
                    "mobile": "13800000000",
                    "job_number": "E-001",
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
        self.assertEqual(user.email, "ada@example.invalid")
        self.assertEqual(user.mobile, "13800000000")
        self.assertEqual(user.job_number, "E-001")
        status = DingTalkDirectorySyncStatus.objects.get()
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.SUCCESS)
        self.assertEqual(status.generation, 1)
        self.assertIsNotNone(status.last_success_at)
        self.assertFalse(DingTalkDirectoryDepartmentStage.objects.filter(corp_id="CORP").exists())
        self.assertFalse(DingTalkDirectoryUserStage.objects.filter(corp_id="CORP").exists())

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_sync_dedupes_users_listed_in_multiple_departments(self, client_cls):
        listed = {
            "userid": "USER",
            "name": "Ada",
            "dept_id_list": [1, 2],
            "active": True,
        }
        client = client_cls.return_value
        client.get_user_detail.return_value = {"manager_userid": "MANAGER"}
        client.iter_departments.return_value = [
            {"dept_id": "2", "name": "Engineering", "parent_dept_id": "1", "raw": {"dept_id": 2}},
        ]
        client.iter_department_users.side_effect = [[listed], [listed]]

        result = sync_dingtalk_directory(self.source, corp_id="CORP")

        self.assertEqual(result["users"], 1)
        self.assertEqual(DingTalkDirectoryUser.objects.filter(user_id="USER").count(), 1)
        self.assertEqual(client.get_user_detail.call_count, 1)

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
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertEqual(status.error, DINGTALK_SYNC_ERROR_INVALID_RESPONSE)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_INVALID_RESPONSE)
        self.assertIsNotNone(status.error_correlation_id)
        self.assertEqual(status.generation, 7)
        self.assertIsNone(status.last_success_at)

    @patch("authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info")
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_corp_verification_accepts_every_shape_dingtalk_reports_the_corp_in(
        self, client_cls, org_auth_mock
    ):
        """authInfos nests and spells the corp id differently per API generation and app type."""
        client_cls.return_value.iter_departments.return_value = []
        client_cls.return_value.iter_department_users.return_value = []
        for raw in [
            {"auth_org_info": {"corpid": "CORP"}},
            {"authCorpInfo": {"corpid": "CORP", "corpName": "Example"}},
            {"authInfos": [{"targetCorpId": "CORP", "contactName": "Example"}]},
            {"result": {"corpId": "CORP"}},
            {"corp_id": "CORP"},
            # Internal-app authInfos: business-license payload, no corp id field.
            {
                "orgName": "Example",
                "licenseOrgName": "Example",
                "authLevel": 2,
                "unifiedSocialCredit": "X",
            },
        ]:
            with self.subTest(raw=raw):
                org_auth_mock.return_value = {"raw": raw, "label": "Example"}

                sync_dingtalk_directory(self.source, corp_id="CORP")

                status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
                self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.SUCCESS)

    @patch("authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info")
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_authorization_and_credential_failures_are_reported_distinctly(
        self, client_cls, org_auth_mock
    ):
        """These used to collapse into "DingTalk returned an invalid directory response"."""
        client_cls.return_value.iter_departments.return_value = []
        for exc, expected in [
            (
                DingTalkDepartmentCorpUnavailable("not authorized"),
                DINGTALK_SYNC_ERROR_CORP_UNAUTHORIZED,
            ),
            (
                DingTalkAppTokenError("app token request failed."),
                DINGTALK_SYNC_ERROR_APP_TOKEN_FAILED,
            ),
        ]:
            with self.subTest(error=type(exc).__name__):
                org_auth_mock.side_effect = exc

                with self.assertRaises(ValueError):
                    sync_dingtalk_directory(self.source, corp_id="CORP")

                status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
                self.assertEqual(status.error_code, expected)

    def test_org_lookup_transport_failure_is_reported_as_an_http_failure(self):
        """DingTalkDepartmentLoadFailed wraps the real cause; keep the cause's meaning."""
        response = Response()
        response.status_code = 503
        exc = DingTalkDepartmentLoadFailed("DingTalk organization lookup failed.")
        exc.__cause__ = HTTPError(response=response)

        code, params = classify_dingtalk_sync_error(exc)

        self.assertEqual(code, DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED)
        self.assertEqual(params, {"status_code": 503})

    def test_out_of_order_run_cannot_publish_over_newer_snapshot(self):
        first_started = now()
        first_run_id, first_sequence = _start_sync_run(self.source, "CORP", first_started)
        second_started = now()
        second_run_id, second_sequence = _start_sync_run(self.source, "CORP", second_started)
        DingTalkDirectoryDepartmentStage.objects.create(
            source=self.source,
            corp_id="CORP",
            run_id=second_run_id,
            dept_id="2",
            name="New",
            parent_dept_id="1",
            raw={},
        )

        second = _publish_snapshot(
            self.source,
            "CORP",
            second_started,
            [],
            (second_run_id, second_sequence),
        )
        stale = _publish_snapshot(
            self.source,
            "CORP",
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

    def test_publish_snapshot_uses_staging_bulk_finalize_query_boundary(self):
        started = now()
        run_id, sequence = _start_sync_run(self.source, "CORP", started)
        for index in range(5):
            DingTalkDirectoryDepartmentStage.objects.create(
                source=self.source,
                corp_id="CORP",
                run_id=run_id,
                dept_id=str(index),
                name=f"Department {index}",
                parent_dept_id="1",
                raw={"dept_id": index},
            )
            DingTalkDirectoryUserStage.objects.create(
                source=self.source,
                corp_id="CORP",
                run_id=run_id,
                user_id=f"USER{index}",
                name=f"User {index}",
                dept_id_list=[str(index)],
                raw={"userid": f"USER{index}"},
            )

        with (
            patch.object(DingTalkDirectoryDepartment.objects, "update_or_create") as dept_update,
            patch.object(DingTalkDirectoryUser.objects, "update_or_create") as user_update,
            CaptureQueriesContext(connection) as captured,
        ):
            counters = _publish_snapshot(self.source, "CORP", started, [], (run_id, sequence))

        self.assertEqual(counters["departments"], 5)
        self.assertEqual(counters["users"], 5)
        self.assertFalse(dept_update.called)
        self.assertFalse(user_update.called)
        self.assertLessEqual(len(captured), 12)

    def test_http_error_status_never_persists_request_url_or_token(self):
        response = Response()
        response.status_code = 403
        response.url = "https://oapi.dingtalk.com/path?access_token=SECRET_TOKEN"
        error = HTTPError(f"403 for url: {response.url}", response=response)

        detail = safe_dingtalk_sync_error(error)

        self.assertEqual(detail, DINGTALK_SYNC_ERROR_HTTP_REQUEST_FAILED)
        self.assertNotIn("SECRET_TOKEN", detail)
        self.assertNotIn("access_token", detail)

    def test_sync_error_log_redacts_dingtalk_secret_detail(self):
        run_id, _enqueued = queue_dingtalk_directory_sync(self.source, "CORP")
        exc = ValueError(
            "provider prose "
            "access_token=SNAKE_ACCESS accessToken=CAMEL_ACCESS "
            "refresh_token=SNAKE_REFRESH refreshToken=CAMEL_REFRESH "
            "client_secret=SNAKE_CLIENT clientSecret=CAMEL_CLIENT "
            "appsecret=APP_SECRET consumerSecret=CONSUMER_SECRET "
            "x-acs-dingtalk-access-token=HEADER_ACCESS "
            "xAcsDingtalkRefreshToken=HEADER_REFRESH "
            "Authorization: Bearer AUTH_SECRET "
            "{'clientSecret': 'DICT_CLIENT', 'refreshToken': 'DICT_REFRESH'} "
            "https://api.example.invalid/path?accessToken=QUERY_ACCESS&"
            "refreshToken=QUERY_REFRESH&corpId=CORP"
        )

        with capture_logs() as logs:
            finalized = finalize_dingtalk_directory_sync_error(
                source=self.source,
                corp_id="CORP",
                run_id=run_id,
                exc=exc,
            )

        self.assertTrue(finalized)
        event = next(log for log in logs if log["event"] == "dingtalk_directory_sync_failed")
        self.assertEqual(event["error_code"], DINGTALK_SYNC_ERROR_INVALID_RESPONSE)
        self.assertEqual(event["corp_id"], "CORP")
        self.assertEqual(event["run_id"], str(run_id))
        self.assertTrue(event["error_correlation_id"])
        self.assertIn("[redacted]", event["error_detail"])
        for secret in (
            "SNAKE_ACCESS",
            "CAMEL_ACCESS",
            "SNAKE_REFRESH",
            "CAMEL_REFRESH",
            "SNAKE_CLIENT",
            "CAMEL_CLIENT",
            "APP_SECRET",
            "CONSUMER_SECRET",
            "HEADER_ACCESS",
            "HEADER_REFRESH",
            "AUTH_SECRET",
            "DICT_CLIENT",
            "DICT_REFRESH",
            "QUERY_ACCESS",
            "QUERY_REFRESH",
        ):
            self.assertNotIn(secret, str(event))

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
        self.assertTrue(
            DingTalkDirectoryDepartment.objects.filter(
                source=self.source, corp_id="CORP", dept_id="1", is_deleted=False
            ).exists()
        )
        self.assertEqual(result["users"], 0)
        self.assertEqual(result["departments"], 1)
        self.assertEqual(status.counters["users"], 0)
        self.assertEqual(status.counters["departments"], 1)
        self.assertEqual(status.generation, 1)
        self.assertFalse(DingTalkDirectoryDepartmentStage.objects.filter(corp_id="CORP").exists())
        self.assertFalse(DingTalkDirectoryUserStage.objects.filter(corp_id="CORP").exists())

        sync_dingtalk_directory(self.source, corp_id="CORP")

        status.refresh_from_db()
        self.assertEqual(status.generation, 2)

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_user_detail_failure_does_not_publish_damaged_manager_snapshot(self, client_cls):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status=DingTalkDirectorySyncStatusChoices.SUCCESS,
            generation=4,
            finished_at=seen,
            last_success_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            manager_user_id="BOSS",
            dept_id_list=["1"],
            last_seen_at=seen,
        )
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.return_value = [
            {"userid": "USER", "name": "Ada", "dept_id_list": [1], "active": True}
        ]
        client.get_user_detail.side_effect = RequestException("timeout")

        with self.assertRaisesMessage(ValueError, "user detail failed"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        cached = DingTalkDirectoryUser.objects.get(
            source=self.source, corp_id="CORP", user_id="USER"
        )
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(cached.manager_user_id, "BOSS")
        self.assertFalse(cached.is_deleted)
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertEqual(status.generation, 4)
        self.assertEqual(status.last_success_at, seen)

    @patch("authentik.sources.oauth.dingtalk.sync.DINGTALK_STAGE_BATCH_SIZE", 1)
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_failed_chunk_keeps_durable_staging_checkpoint(self, client_cls):
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.return_value = [
            {"userid": "USER", "name": "Ada", "dept_id_list": [1], "active": True}
        ]
        client.get_user_detail.side_effect = RequestException("timeout")

        with self.assertRaisesMessage(ValueError, "user detail failed"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertEqual(status.counters["departments_staged"], 1)
        self.assertEqual(status.counters["checkpoint"], {"dept_id": "1"})
        self.assertTrue(
            DingTalkDirectoryDepartmentStage.objects.filter(
                source=self.source, corp_id="CORP", dept_id="1"
            ).exists()
        )

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_non_boolean_active_is_rejected(self, client_cls):
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.return_value = [
            {"userid": "USER", "name": "Ada", "dept_id_list": [1], "active": "false"}
        ]

        with self.assertRaisesMessage(ValueError, "active"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

    @patch("authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info")
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_corp_verification_mismatch_does_not_publish(self, client_cls, org_auth_mock):
        # The echoed targetCorpId is the value this request sent, so it must not vouch
        # for a response that names a different corp as the authorized one.
        org_auth_mock.return_value = {
            "raw": {
                "auth_org_info": {"corpid": "OTHER"},
                "authInfos": [{"targetCorpId": "CORP"}],
            }
        }
        client_cls.return_value.iter_departments.return_value = []

        with self.assertRaisesMessage(ValueError, "different corp identity"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_CORP_MISMATCH)
        self.assertEqual(status.error_params["reason"], "mismatch")
        self.assertEqual(status.generation, 0)
        self.assertFalse(DingTalkDirectoryUser.objects.filter(corp_id="CORP").exists())

    @patch("authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info")
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_corp_verification_requires_response_identity(self, client_cls, org_auth_mock):
        org_auth_mock.return_value = {"raw": {"authUserInfo": {"userId": "USER"}}}
        client_cls.return_value.iter_departments.return_value = []

        with self.assertRaisesMessage(ValueError, "did not report a corp identity"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_CORP_MISMATCH)
        self.assertEqual(status.error_params["reason"], "unverified")

    @patch("authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info")
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_corp_verification_accepts_internal_app_license_payload_without_corp_id(
        self, client_cls, org_auth_mock
    ):
        org_auth_mock.return_value = {
            "corp_id": "CORP",
            "label": "Example Co",
            "raw": {
                "orgName": "Example Co",
                "licenseOrgName": "Example Co",
                "authLevel": 2,
            },
        }
        client_cls.return_value.iter_departments.return_value = []
        client_cls.return_value.iter_department_users.return_value = []

        sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.SUCCESS)

    @patch("authentik.sources.oauth.dingtalk.sync.DINGTALK_MAX_SYNC_USERS", 0)
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_global_user_limit_aborts_without_publishing(self, client_cls):
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.return_value = [
            {"userid": "USER", "name": "Ada", "dept_id_list": [1], "active": True}
        ]

        with self.assertRaisesMessage(ValueError, "user limit"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertFalse(DingTalkDirectoryUser.objects.filter(corp_id="CORP").exists())

    @patch("authentik.sources.oauth.dingtalk.sync.DINGTALK_MAX_RAW_PAYLOAD_BYTES", 1)
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_raw_payload_budget_aborts_without_publishing(self, client_cls):
        client = client_cls.return_value
        client.iter_departments.return_value = []
        client.iter_department_users.return_value = []

        with self.assertRaisesMessage(ValueError, "payload limit"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        self.assertFalse(DingTalkDirectoryDepartment.objects.filter(corp_id="CORP").exists())
        self.assertFalse(DingTalkDirectoryDepartmentStage.objects.filter(corp_id="CORP").exists())

    @patch("authentik.sources.oauth.dingtalk.sync.DINGTALK_MAX_CONCURRENT_SYNCS", 0)
    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_concurrency_budget_aborts_without_publishing(self, client_cls):
        client_cls.return_value.iter_departments.return_value = []

        with self.assertRaisesMessage(ValueError, "concurrency budget"):
            sync_dingtalk_directory(self.source, corp_id="CORP")

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertFalse(DingTalkDirectoryDepartment.objects.filter(corp_id="CORP").exists())

    def test_queue_single_flight_reuses_active_run(self):
        first_run, first_enqueued = queue_dingtalk_directory_sync(self.source, "CORP")
        second_run, second_enqueued = queue_dingtalk_directory_sync(self.source, "CORP")

        self.assertTrue(first_enqueued)
        self.assertFalse(second_enqueued)
        self.assertEqual(first_run, second_run)
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.QUEUED)

    def test_queued_run_marks_error_when_source_disabled_before_worker_starts(self):
        run_id, _enqueued = queue_dingtalk_directory_sync(self.source, "CORP")
        self.source.enabled = False
        self.source.save(update_fields=["enabled"])

        result = dingtalk_directory_sync_task(str(self.source.pk), "CORP", str(run_id))

        self.assertIsNone(result)
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertIsNone(status.active_run_id)
        self.assertEqual(status.error, DINGTALK_SYNC_ERROR_SOURCE_DISABLED)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_SOURCE_DISABLED)
        self.assertIsNotNone(status.error_correlation_id)

    @patch("authentik.sources.oauth.types.dingtalk.get_dingtalk_allowlist_binding")
    @patch("authentik.sources.oauth.tasks.dingtalk_directory_sync.send")
    def test_scheduled_sync_uses_source_scoped_corp_identity(
        self,
        send_mock,
        allowlist_mock,
    ):
        allowlist_mock.return_value = (None, None, {"companies": []})
        other_source = OAuthSource.objects.create(
            name="Other DingTalk",
            slug="dingtalk-other",
            provider_type="dingtalk",
            consumer_key="OTHER_CLIENT_ID",
            consumer_secret="OTHER_CLIENT_SECRET",
        )
        user = create_test_user("scheduled-source-scope")
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

        dingtalk_directory_sync_all()

        queued = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP_A")
        self.assertEqual(queued.status, DingTalkDirectorySyncStatusChoices.QUEUED)
        self.assertEqual(send_mock.call_args.args[1], "CORP_A")
        self.assertFalse(
            DingTalkDirectorySyncStatus.objects.filter(
                source=self.source, corp_id="CORP_B"
            ).exists()
        )

    @patch("authentik.sources.oauth.types.dingtalk.get_dingtalk_allowlist_binding")
    @patch("authentik.sources.oauth.tasks.dingtalk_directory_sync.send")
    def test_scheduled_sync_marks_single_corp_error_when_broker_rejects(
        self,
        send_mock,
        allowlist_mock,
    ):
        send_mock.side_effect = RuntimeError("broker secret detail")
        allowlist_mock.return_value = (None, None, {"companies": [{"corp_id": "CORP"}]})

        dingtalk_directory_sync_all()

        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertIsNone(status.active_run_id)
        self.assertEqual(status.error, DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE)
        self.assertIsNotNone(status.error_correlation_id)


class TestDingTalkCorpIdExtraction(TestCase):
    def test_collects_every_spelling_and_nesting_of_the_corp_id(self):
        self.assertEqual(
            extract_dingtalk_corp_ids(
                {
                    "authCorpInfo": {"corpid": "A"},
                    "result": {"auth_corp_id": "B"},
                }
            ),
            {"A", "B"},
        )

    def test_ignores_the_echoed_request_corp_unless_asked_for_it(self):
        """The echo is the value we sent, so it must not vouch for a contradicting response."""
        raw = {"authCorpInfo": {"corpid": "REAL"}, "authInfos": [{"targetCorpId": "REQUESTED"}]}

        self.assertEqual(extract_dingtalk_corp_ids(raw), {"REAL"})
        self.assertEqual(
            extract_dingtalk_corp_ids(raw, keys=DINGTALK_CORP_ID_ECHO_KEYS), {"REQUESTED"}
        )

    def test_normalizes_numeric_ids_and_ignores_non_identifier_values(self):
        self.assertEqual(
            extract_dingtalk_corp_ids(
                {"corpId": 12345, "nested": {"corp_id": True, "other": {"corpid": ""}}}
            ),
            {"12345"},
        )

    def test_stops_walking_beyond_the_depth_bound(self):
        deep: dict = {"corpid": "DEEP"}
        for _ in range(12):
            deep = {"nested": deep}

        self.assertEqual(extract_dingtalk_corp_ids(deep), set())

    def test_returns_empty_for_payloads_without_a_corp_identity(self):
        self.assertEqual(extract_dingtalk_corp_ids({"authUserInfo": {"userId": "USER"}}), set())


class TestDingTalkOrgContext(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def test_org_context_accepts_directory_identity_without_a_user_pk(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            last_success_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            dept_id_list=[],
            last_seen_at=seen,
        )

        context = get_dingtalk_org_context(
            SimpleNamespace(attributes={"dingtalk": {"corp_id": "CORP", "user_id": "USER"}}),
            source_slug="dingtalk",
        )

        self.assertEqual(context["corp_id"], "CORP")
        self.assertEqual(context["user_id"], "USER")
        self.assertFalse(context["stale"])

    def test_org_context_returns_department_path_and_manager_chain(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            last_success_at=seen,
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

    def test_org_context_uses_source_scoped_identity_before_legacy_attributes(self):
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
        user = create_test_user("multi-source")
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
        UserOAuthSourceConnection.objects.create(
            user=user,
            source=other_source,
            identifier="UNION_B",
        )

        context = get_dingtalk_org_context(user, source_slug="dingtalk")

        self.assertEqual(context["corp_id"], "CORP_A")
        self.assertEqual(context["user_id"], "USER_A")

    def test_org_context_uses_canonical_source_scoped_dict_identity(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            last_success_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            dept_id_list=[],
            last_seen_at=seen,
        )
        user = create_test_user("canonical-dict")
        user.attributes = {
            "dingtalk_sources": {
                str(self.source.pk): {"corp_id": "CORP", "user_id": "USER"},
            },
        }
        user.save()

        context = get_dingtalk_org_context(user, source_slug="dingtalk")

        self.assertEqual(context["corp_id"], "CORP")
        self.assertEqual(context["user_id"], "USER")

    def test_org_context_tolerates_transitional_list_identity_by_using_latest_entry(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP_A",
            status="success",
            finished_at=seen,
            last_success_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP_A",
            user_id="USER_A",
            name="Ada",
            dept_id_list=[],
            last_seen_at=seen,
        )
        user = create_test_user("transitional-list")
        user.attributes = {
            "dingtalk": {"corp_id": "CORP_A", "user_id": "USER_A"},
            "dingtalk_sources": [
                {
                    "source_pk": str(self.source.pk),
                    "source_slug": "old-dingtalk",
                    "corp_id": "CORP_A",
                    "user_id": "USER_A",
                },
                {
                    "source_pk": str(self.source.pk),
                    "source_slug": self.source.slug,
                    "corp_id": "CORP_A",
                    "user_id": "USER_A",
                },
            ],
        }
        user.save()

        context = get_dingtalk_org_context(user, source_slug="dingtalk")

        self.assertEqual(context["corp_id"], "CORP_A")
        self.assertEqual(context["user_id"], "USER_A")

    def test_org_context_matches_list_identity_by_source_pk_after_slug_rename(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            last_success_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            dept_id_list=[],
            last_seen_at=seen,
        )
        old_slug = self.source.slug
        self.source.slug = "renamed-dingtalk"
        self.source.save(update_fields=["slug"])
        user = create_test_user("renamed-source")
        user.attributes = {
            "dingtalk_sources": {
                str(self.source.pk): {
                    "source_pk": str(self.source.pk),
                    "source_slug": old_slug,
                    "corp_id": "CORP",
                    "user_id": "USER",
                },
            },
        }
        user.save()

        context = get_dingtalk_org_context(user, source_slug="renamed-dingtalk")

        self.assertEqual(context["corp_id"], "CORP")
        self.assertEqual(context["user_id"], "USER")

    def test_org_context_query_count_is_bounded_for_departments_and_managers(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            last_success_at=seen,
        )
        for dept_id, parent_id in (("1", ""), ("2", "1"), ("3", "2")):
            DingTalkDirectoryDepartment.objects.create(
                source=self.source,
                corp_id="CORP",
                dept_id=dept_id,
                name=f"Dept {dept_id}",
                parent_dept_id=parent_id,
                last_seen_at=seen,
            )
        previous = ""
        for user_id in ("CEO", "VP", "MANAGER"):
            DingTalkDirectoryUser.objects.create(
                source=self.source,
                corp_id="CORP",
                user_id=user_id,
                name=user_id,
                manager_user_id=previous,
                dept_id_list=["1"],
                last_seen_at=seen,
            )
            previous = user_id
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            manager_user_id="MANAGER",
            dept_id_list=["1", "2", "3"],
            last_seen_at=seen,
        )
        user = create_test_user("bounded")
        user.attributes = {
            "dingtalk_sources": {
                str(self.source.pk): {
                    "source_pk": str(self.source.pk),
                    "source_slug": self.source.slug,
                    "corp_id": "CORP",
                    "user_id": "USER",
                },
            },
        }
        user.save()

        with self.assertNumQueries(5):
            context = get_dingtalk_org_context(user, source_slug="dingtalk")

        self.assertEqual([dept["dept_id"] for dept in context["departments"]], ["1", "2", "3"])
        self.assertEqual(
            [manager["user_id"] for manager in context["manager_chain"]],
            ["MANAGER", "VP", "CEO"],
        )

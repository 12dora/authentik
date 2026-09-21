"""DingTalk API usage buckets, policy enforcement, and endpoints."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from requests_mock import Mocker
from rest_framework.test import APITestCase

from authentik.core.tests.utils import create_test_admin_user, create_test_user
from authentik.lib.utils.http import get_http_session
from authentik.sources.oauth.dingtalk.client import (
    DINGTALK_DEPARTMENT_USER_LIST_URL,
    DingTalkDirectoryClient,
)
from authentik.sources.oauth.dingtalk.sync import (
    DINGTALK_SYNC_ERROR_USAGE_POLICY_BLOCKED,
    sync_dingtalk_directory,
)
from authentik.sources.oauth.dingtalk.usage import (
    CATEGORY_ALLOWLIST,
    CATEGORY_AUTH_INFO,
    CATEGORY_DIRECTORY_FULL,
    CATEGORY_DIRECTORY_INCREMENTAL,
    CATEGORY_LOGIN,
    CATEGORY_TOKEN,
    INTERNAL_USAGE_CATEGORIES,
    THROTTLE_CATEGORY_P2,
    USAGE_POLICY_BLOCKED_MESSAGE,
    DingTalkUsagePolicyBlocked,
    _increment_throttle,
    check,
    clear_policy_memo,
    current_hour_start,
    directory_usage_category,
    format_utc_z,
    prepare_outbound_call,
    purge_expired_usage_buckets,
    store_policy,
)
from authentik.sources.oauth.models import (
    DingTalkApiUsageBucket,
    DingTalkDirectorySyncStatus,
    DingTalkDirectorySyncStatusChoices,
    OAuthSource,
)
from authentik.sources.oauth.tasks import dingtalk_directory_sync_all
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ACCESS_TOKEN_URL,
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_GET_BY_UNION_ID_URL,
    DINGTALK_ORG_AUTH_INFO_URL,
    DINGTALK_PROFILE_URL,
    DINGTALK_USER_DETAIL_URL,
    DingTalkAppTokenError,
    DingTalkOAuth2Client,
    _fetch_dingtalk_app_token,
    _fetch_dingtalk_user_profile,
    fetch_dingtalk_app_token_cached,
    fetch_dingtalk_departments,
    fetch_dingtalk_org_auth_info,
)


def _future_expires(hours: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


class DingTalkUsageTestCase(TestCase):
    def setUp(self):
        cache.clear()
        clear_policy_memo()
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            enabled=True,
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def counts(self, category: str) -> tuple[int, int]:
        row = DingTalkApiUsageBucket.objects.filter(source=self.source, category=category).first()
        if row is None:
            return 0, 0
        return row.count, row.blocked_count

    def push_policy(self, **overrides):
        payload = {
            "blocked_priorities": [],
            "throttle_per_hour": {"p1": None, "p2": None},
            "block_p0_billed": False,
            "expires_at": _future_expires(),
            **overrides,
        }
        return store_policy(self.source, payload)


class TestDingTalkUsageRecording(DingTalkUsageTestCase):
    def test_gettoken_counts_ak_token(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            token = _fetch_dingtalk_app_token(self.source, get_http_session())

        self.assertEqual(token, "APP_TOKEN")
        self.assertEqual(self.counts(CATEGORY_TOKEN), (1, 0))

    def test_cached_gettoken_does_not_count(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            fetch_dingtalk_app_token_cached(self.source)
            fetch_dingtalk_app_token_cached(self.source)

        self.assertEqual(self.counts(CATEGORY_TOKEN), (1, 0))

    def test_auth_info_counts_ak_auth_info(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"authOrgInfo": {"corpId": "CORP", "corpName": "Example"}},
            )
            fetch_dingtalk_org_auth_info(self.source, "CORP")

        self.assertEqual(self.counts(CATEGORY_AUTH_INFO), (1, 0))
        self.assertEqual(self.counts(CATEGORY_TOKEN), (1, 0))

    def test_auth_info_retries_are_counted(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                [
                    {"status_code": 401, "json": {"code": "InvalidAuthentication"}},
                    {"json": {"authOrgInfo": {"corpId": "CORP"}}},
                ],
            )
            fetch_dingtalk_org_auth_info(self.source, "CORP")

        self.assertEqual(self.counts(CATEGORY_AUTH_INFO), (2, 0))

    def test_directory_mode_selects_category(self):
        self.assertEqual(directory_usage_category(full=True), CATEGORY_DIRECTORY_FULL)
        self.assertEqual(directory_usage_category(full=False), CATEGORY_DIRECTORY_INCREMENTAL)
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0, "result": []})
            list(DingTalkDirectoryClient(self.source, full=True).iter_departments())
            list(DingTalkDirectoryClient(self.source, full=False).iter_departments())

        self.assertEqual(self.counts(CATEGORY_DIRECTORY_FULL), (1, 0))
        self.assertEqual(self.counts(CATEGORY_DIRECTORY_INCREMENTAL), (1, 0))

    def test_directory_retries_are_counted(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {"status_code": 429, "json": {"errcode": 88}},
                    {"json": {"errcode": 0, "result": []}},
                ],
            )
            list(
                DingTalkDirectoryClient(
                    self.source, full=True, sleeper=lambda _s: None
                ).iter_departments()
            )

        self.assertEqual(self.counts(CATEGORY_DIRECTORY_FULL), (2, 0))

    def test_directory_user_list_and_detail_use_client_category(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_DEPARTMENT_USER_LIST_URL,
                json={"errcode": 0, "result": {"list": [], "has_more": False}},
            )
            mocker.post(
                DINGTALK_USER_DETAIL_URL,
                json={"errcode": 0, "result": {"userid": "USER"}},
            )
            client = DingTalkDirectoryClient(self.source, full=False)
            list(client.iter_department_users("1"))
            client.get_user_detail("USER")

        self.assertEqual(self.counts(CATEGORY_DIRECTORY_INCREMENTAL), (2, 0))

    def test_directory_token_failure_is_not_counted_as_directory(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"errcode": 40001})
            with self.assertRaises(DingTalkAppTokenError):
                list(DingTalkDirectoryClient(self.source, full=True).iter_departments())

        self.assertEqual(self.counts(CATEGORY_DIRECTORY_FULL), (0, 0))
        self.assertEqual(self.counts(CATEGORY_TOKEN), (1, 0))

    def test_recording_is_single_on_conflict_statement(self):
        with CaptureQueriesContext(connection) as captured:
            prepare_outbound_call(self.source, CATEGORY_LOGIN)
        bucket_sql = [
            query["sql"]
            for query in captured.captured_queries
            if "dingtalkapiusagebucket" in query["sql"].lower()
        ]
        self.assertEqual(len(bucket_sql), 1)
        self.assertIn("ON CONFLICT", bucket_sql[0].upper())

    def test_allowlist_walk_counts_ak_allowlist(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"authOrgInfo": {"corpId": "CORP", "corpName": "Example"}},
            )
            mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0, "result": []})
            fetch_dingtalk_departments(self.source, "CORP")

        self.assertEqual(self.counts(CATEGORY_ALLOWLIST), (1, 0))
        self.assertEqual(self.counts(CATEGORY_AUTH_INFO), (1, 0))

    def test_login_token_and_profile_count_ak_login(self):
        request = RequestFactory().get("/", {"authCode": "AUTH_CODE", "state": "STATE"})
        request.session = {f"oauth-client-{self.source.name}-request-state": "STATE"}
        client = DingTalkOAuth2Client(self.source, request, callback="/cb")
        with Mocker() as mocker:
            mocker.post(
                DINGTALK_ACCESS_TOKEN_URL,
                json={"accessToken": "USER_TOKEN", "corpId": "CORP"},
            )
            mocker.get(
                DINGTALK_PROFILE_URL,
                json={"unionId": "UNION", "openId": "OPEN", "nick": "Ada"},
            )
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_GET_BY_UNION_ID_URL,
                json={"errcode": 0, "result": {"userid": "USER"}},
            )
            mocker.post(
                DINGTALK_USER_DETAIL_URL,
                json={"errcode": 0, "result": {"userid": "USER", "name": "Ada"}},
            )
            token = client.get_access_token()
            profile = client.get_profile_info(token)

        self.assertEqual(token["access_token"], "USER_TOKEN")
        self.assertEqual(profile["userid"], "USER")
        self.assertEqual(self.counts(CATEGORY_LOGIN), (4, 0))

    def test_allowlist_discovery_user_calls_count_ak_login(self):
        with Mocker() as mocker:
            mocker.post(
                DINGTALK_ACCESS_TOKEN_URL,
                json={"accessToken": "USER_TOKEN", "corpId": "CORP"},
            )
            mocker.get(
                DINGTALK_PROFILE_URL,
                json={"unionId": "UNION", "nick": "Ada", "corpId": "CORP"},
            )
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"authOrgInfo": {"corpId": "CORP", "corpName": "Example"}},
            )
            _fetch_dingtalk_user_profile(self.source, "AUTH_CODE", get_http_session())

        self.assertEqual(self.counts(CATEGORY_LOGIN), (2, 0))
        self.assertEqual(self.counts(CATEGORY_AUTH_INFO), (1, 0))

    def test_recording_failure_does_not_break_calls(self):
        with (
            patch(
                "authentik.sources.oauth.dingtalk.usage._increment_bucket",
                side_effect=RuntimeError("db down"),
            ),
            Mocker() as mocker,
        ):
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            token = fetch_dingtalk_app_token_cached(self.source)

        self.assertEqual(token, "APP_TOKEN")
        self.assertEqual(DingTalkApiUsageBucket.objects.count(), 0)

    def test_sync_passes_full_flag_to_client(self):
        with (
            patch(
                "authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info",
                return_value={"raw": {"corpid": "CORP"}, "label": "Example"},
            ),
            patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient") as client_cls,
        ):
            client_cls.return_value.iter_departments.return_value = []
            client_cls.return_value.iter_department_users.return_value = []
            sync_dingtalk_directory(self.source, "CORP", full=True)
            self.assertTrue(client_cls.call_args.kwargs["full"])
            sync_dingtalk_directory(self.source, "CORP", full=False)
            self.assertFalse(client_cls.call_args.kwargs["full"])


class TestDingTalkUsagePolicy(DingTalkUsageTestCase):
    def test_missing_and_expired_policy_fail_open(self):
        prepare_outbound_call(self.source, CATEGORY_ALLOWLIST)
        cache.set(
            f"authentik/sources/oauth/dingtalk/usage/policy/{self.source.pk}",
            {
                "blocked_priorities": ["p1", "p2"],
                "throttle_per_hour": {},
                "block_p0_billed": True,
                "expires_at": format_utc_z(datetime.now(UTC) - timedelta(minutes=1)),
            },
            timeout=3600,
        )
        prepare_outbound_call(self.source, CATEGORY_ALLOWLIST)
        self.assertEqual(self.counts(CATEGORY_ALLOWLIST), (2, 0))

    def test_token_never_blocked(self):
        self.push_policy(
            blocked_priorities=["p0", "p1", "p2"],
            throttle_per_hour={"p0": 0, "p1": 0, "p2": 0},
            block_p0_billed=True,
        )
        with Mocker() as mocker:
            gettoken = mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            fetch_dingtalk_app_token_cached(self.source)

        self.assertEqual(gettoken.call_count, 1)
        self.assertEqual(self.counts(CATEGORY_TOKEN), (1, 0))

    def test_p0_billed_refused_only_when_block_p0_billed(self):
        self.push_policy(blocked_priorities=["p0"], block_p0_billed=False)
        prepare_outbound_call(self.source, CATEGORY_LOGIN)
        self.push_policy(blocked_priorities=[], block_p0_billed=True)
        with self.assertRaises(DingTalkUsagePolicyBlocked) as raised:
            prepare_outbound_call(self.source, CATEGORY_LOGIN)
        self.assertIn("usage policy", str(raised.exception))
        self.assertEqual(self.counts(CATEGORY_LOGIN), (1, 1))

    def test_p1_and_p2_blocked_priorities(self):
        self.push_policy(blocked_priorities=["p1"])
        with self.assertRaises(DingTalkUsagePolicyBlocked):
            check(self.source, CATEGORY_AUTH_INFO)
        with self.assertRaises(DingTalkUsagePolicyBlocked):
            check(self.source, CATEGORY_DIRECTORY_INCREMENTAL)
        prepare_outbound_call(self.source, CATEGORY_DIRECTORY_FULL)
        self.push_policy(blocked_priorities=["p2"])
        with self.assertRaises(DingTalkUsagePolicyBlocked):
            check(self.source, CATEGORY_DIRECTORY_FULL)
        with self.assertRaises(DingTalkUsagePolicyBlocked):
            check(self.source, CATEGORY_ALLOWLIST)
        prepare_outbound_call(self.source, CATEGORY_AUTH_INFO)

    def test_throttle_per_hour(self):
        self.push_policy(throttle_per_hour={"p1": None, "p2": 1})
        prepare_outbound_call(self.source, CATEGORY_ALLOWLIST)
        with self.assertRaises(DingTalkUsagePolicyBlocked):
            prepare_outbound_call(self.source, CATEGORY_ALLOWLIST)
        prepare_outbound_call(self.source, CATEGORY_AUTH_INFO)
        prepare_outbound_call(self.source, CATEGORY_AUTH_INFO)
        self.assertEqual(self.counts(CATEGORY_ALLOWLIST), (1, 1))

    def test_blocked_call_sends_no_http_and_is_not_retried(self):
        self.push_policy(blocked_priorities=["p2"])
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            post = mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0, "result": []})
            with self.assertRaises(DingTalkUsagePolicyBlocked):
                list(DingTalkDirectoryClient(self.source, full=True).iter_departments())

        self.assertEqual(post.call_count, 0)
        self.assertEqual(self.counts(CATEGORY_DIRECTORY_FULL), (0, 1))
        self.assertEqual(self.counts(CATEGORY_TOKEN), (0, 0))

    def test_login_refusal_is_a_clean_error(self):
        self.push_policy(block_p0_billed=True)
        request = RequestFactory().get("/", {"authCode": "AUTH_CODE", "state": "STATE"})
        request.session = {f"oauth-client-{self.source.name}-request-state": "STATE"}
        client = DingTalkOAuth2Client(self.source, request, callback="/cb")
        with Mocker() as mocker:
            token_mock = mocker.post(DINGTALK_ACCESS_TOKEN_URL, json={"accessToken": "USER"})
            token = client.get_access_token()

        self.assertEqual(token_mock.call_count, 0)
        self.assertEqual(token["error"], USAGE_POLICY_BLOCKED_MESSAGE)
        with Mocker() as mocker:
            profile_mock = mocker.get(DINGTALK_PROFILE_URL, json={"unionId": "UNION"})
            with self.assertRaisesMessage(ValueError, USAGE_POLICY_BLOCKED_MESSAGE):
                client.get_profile_info({"access_token": "USER"})

        self.assertEqual(profile_mock.call_count, 0)

    def test_sync_finalises_with_usage_policy_error(self):
        self.push_policy(blocked_priorities=["p2"])
        with (
            patch(
                "authentik.sources.oauth.dingtalk.sync.fetch_dingtalk_org_auth_info",
                return_value={"raw": {"corpid": "CORP"}, "label": "Example"},
            ),
            Mocker() as mocker,
        ):
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            post = mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0, "result": []})
            result = sync_dingtalk_directory(self.source, "CORP", full=True)

        self.assertIsNone(result)
        self.assertEqual(post.call_count, 0)
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertEqual(status.error, DINGTALK_SYNC_ERROR_USAGE_POLICY_BLOCKED)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_USAGE_POLICY_BLOCKED)
        self.assertEqual(status.error_params["reason"], "blocked by usage policy")
        self.assertIsNone(status.active_run_id)

    def test_incremental_sync_blocked_by_p1_returns_without_raise(self):
        self.push_policy(blocked_priorities=["p1"])
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"auth_org_info": {"corpid": "CORP", "corp_name": "Example"}},
            )
            post = mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0, "result": []})
            result = sync_dingtalk_directory(self.source, "CORP", full=False)

        self.assertIsNone(result)
        self.assertEqual(post.call_count, 0)
        status = DingTalkDirectorySyncStatus.objects.get(source=self.source, corp_id="CORP")
        self.assertEqual(status.status, DingTalkDirectorySyncStatusChoices.ERROR)
        self.assertEqual(status.error_code, DINGTALK_SYNC_ERROR_USAGE_POLICY_BLOCKED)
        self.assertIsNone(status.active_run_id)
        self.assertEqual(self.counts(CATEGORY_DIRECTORY_INCREMENTAL), (0, 1))
        self.assertEqual(self.counts(CATEGORY_AUTH_INFO), (0, 0))

    def test_full_sync_corp_verify_uses_directory_category_not_auth_info(self):
        self.push_policy(blocked_priorities=["p1"])
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.get(
                DINGTALK_ORG_AUTH_INFO_URL,
                json={"auth_org_info": {"corpid": "CORP", "corp_name": "Example"}},
            )
            mocker.post(DINGTALK_DEPARTMENT_LIST_URL, json={"errcode": 0, "result": []})
            mocker.post(
                DINGTALK_DEPARTMENT_USER_LIST_URL,
                json={"errcode": 0, "result": {"list": [], "has_more": False}},
            )
            result = sync_dingtalk_directory(self.source, "CORP", full=True)

        self.assertIsNotNone(result)
        self.assertEqual(self.counts(CATEGORY_AUTH_INFO), (0, 0))
        self.assertGreaterEqual(self.counts(CATEGORY_DIRECTORY_FULL)[0], 1)
        self.assertEqual(self.counts(CATEGORY_DIRECTORY_FULL)[1], 0)

    def test_throttle_returning_upsert_serializes_slots(self):
        self.push_policy(throttle_per_hour={"p1": None, "p2": 1})
        first = _increment_throttle(self.source, "p2")
        second = _increment_throttle(self.source, "p2")
        self.assertEqual([first, second], [1, 2])
        self.assertEqual(sum(1 for value in (first, second) if value <= 1), 1)
        row = DingTalkApiUsageBucket.objects.get(source=self.source, category=THROTTLE_CATEGORY_P2)
        self.assertEqual(row.count, 2)
        self.assertIn(THROTTLE_CATEGORY_P2, INTERNAL_USAGE_CATEGORIES)

    def test_throttle_interleaved_calls_do_not_over_allow(self):
        self.push_policy(throttle_per_hour={"p1": None, "p2": 2})
        outcomes = []
        from authentik.sources.oauth.dingtalk import usage as usage_mod

        real = usage_mod._increment_throttle

        def interleaved(source, priority):
            # Simulate a second worker taking a slot before this call decides.
            real(source, priority)
            return real(source, priority)

        with patch.object(usage_mod, "_increment_throttle", interleaved):
            for _ in range(2):
                try:
                    prepare_outbound_call(self.source, CATEGORY_ALLOWLIST)
                    outcomes.append("allow")
                except DingTalkUsagePolicyBlocked:
                    outcomes.append("block")

        self.assertEqual(outcomes.count("allow"), 1)
        self.assertEqual(outcomes.count("block"), 1)
        self.assertEqual(
            DingTalkApiUsageBucket.objects.get(
                source=self.source, category=THROTTLE_CATEGORY_P2
            ).count,
            4,
        )

    def test_policy_memo_ttl_and_put_invalidation(self):
        clock = {"now": 1000.0}

        def fake_monotonic():
            return clock["now"]

        with patch("authentik.sources.oauth.dingtalk.usage.monotonic", fake_monotonic):
            self.push_policy(blocked_priorities=["p2"])
            with self.assertRaises(DingTalkUsagePolicyBlocked):
                check(self.source, CATEGORY_ALLOWLIST)
            cache.set(
                f"authentik/sources/oauth/dingtalk/usage/policy/{self.source.pk}",
                {
                    "blocked_priorities": [],
                    "throttle_per_hour": {},
                    "block_p0_billed": False,
                    "expires_at": format_utc_z(_future_expires()),
                },
                timeout=3600,
            )
            clock["now"] = 1020.0
            with self.assertRaises(DingTalkUsagePolicyBlocked):
                check(self.source, CATEGORY_ALLOWLIST)
            clock["now"] = 1031.0
            check(self.source, CATEGORY_ALLOWLIST)
            self.push_policy(blocked_priorities=["p2"])
            with self.assertRaises(DingTalkUsagePolicyBlocked):
                check(self.source, CATEGORY_ALLOWLIST)

    def test_purge_deletes_buckets_older_than_sixty_days(self):
        old = DingTalkApiUsageBucket.objects.create(
            source=self.source,
            hour_start=datetime.now(UTC) - timedelta(days=61),
            category=CATEGORY_LOGIN,
            count=9,
        )
        recent = DingTalkApiUsageBucket.objects.create(
            source=self.source,
            hour_start=current_hour_start() - timedelta(days=1),
            category=CATEGORY_LOGIN,
            count=3,
        )
        old_throttle = DingTalkApiUsageBucket.objects.create(
            source=self.source,
            hour_start=datetime.now(UTC) - timedelta(days=61),
            category=THROTTLE_CATEGORY_P2,
            count=4,
        )
        with (
            patch(
                "authentik.sources.oauth.types.dingtalk.get_dingtalk_allowlist_binding",
                return_value=(None, None, None),
            ),
            patch("authentik.sources.oauth.tasks.dingtalk_directory_sync.send"),
        ):
            dingtalk_directory_sync_all()

        self.assertFalse(DingTalkApiUsageBucket.objects.filter(pk=old.pk).exists())
        self.assertFalse(DingTalkApiUsageBucket.objects.filter(pk=old_throttle.pk).exists())
        self.assertTrue(DingTalkApiUsageBucket.objects.filter(pk=recent.pk).exists())
        self.assertEqual(purge_expired_usage_buckets(), 0)


class TestDingTalkUsageAPI(APITestCase):
    def setUp(self):
        cache.clear()
        clear_policy_memo()
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            enabled=True,
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        self.other = OAuthSource.objects.create(
            name="Other",
            slug="other",
            provider_type="dingtalk",
            enabled=True,
            consumer_key="OTHER",
            consumer_secret="OTHER",
        )

    def authenticate(self, user):
        self.client.force_login(user)
        self.client.force_authenticate(user=user)

    def usage_url(self, slug="dingtalk"):
        return reverse("authentik_api:dingtalk-directory-usage", kwargs={"source_slug": slug})

    def policy_url(self, slug="dingtalk"):
        return reverse(
            "authentik_api:dingtalk-directory-usage-policy", kwargs={"source_slug": slug}
        )

    def test_usage_requires_view_permission(self):
        since = format_utc_z(datetime.now(UTC) - timedelta(hours=1))
        response = self.client.get(self.usage_url(), {"since": since})
        self.assertEqual(response.status_code, 403)
        self.authenticate(create_test_user("regular"))
        response = self.client.get(self.usage_url(), {"since": since})
        self.assertEqual(response.status_code, 403)

    def test_usage_shape_filter_and_validation(self):
        hour = current_hour_start()
        older = hour - timedelta(hours=2)
        DingTalkApiUsageBucket.objects.create(
            source=self.source,
            hour_start=older,
            category=CATEGORY_DIRECTORY_FULL,
            count=9,
            blocked_count=1,
        )
        DingTalkApiUsageBucket.objects.create(
            source=self.source,
            hour_start=hour,
            category=CATEGORY_DIRECTORY_FULL,
            count=220,
            blocked_count=0,
        )
        DingTalkApiUsageBucket.objects.create(
            source=self.other,
            hour_start=hour,
            category=CATEGORY_DIRECTORY_FULL,
            count=99,
        )
        self.authenticate(create_test_admin_user())

        missing = self.client.get(self.usage_url())
        self.assertEqual(missing.status_code, 400)
        invalid = self.client.get(self.usage_url(), {"since": "not-a-date"})
        self.assertEqual(invalid.status_code, 400)
        too_old = self.client.get(
            self.usage_url(),
            {"since": format_utc_z(datetime.now(UTC) - timedelta(days=46))},
        )
        self.assertEqual(too_old.status_code, 400)

        since = format_utc_z(hour.replace(minute=30))
        response = self.client.get(self.usage_url(), {"since": since})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["generated_at"].endswith("Z"))
        self.assertEqual(
            payload["buckets"],
            [
                {
                    "hour_start": format_utc_z(hour),
                    "category": CATEGORY_DIRECTORY_FULL,
                    "count": 220,
                    "blocked_count": 0,
                }
            ],
        )

        earlier = self.client.get(self.usage_url(), {"since": format_utc_z(older)})
        self.assertEqual(len(earlier.json()["buckets"]), 2)

        DingTalkApiUsageBucket.objects.create(
            source=self.source,
            hour_start=hour,
            category=THROTTLE_CATEGORY_P2,
            count=7,
        )
        filtered = self.client.get(self.usage_url(), {"since": format_utc_z(hour)})
        self.assertEqual(filtered.status_code, 200)
        categories = [item["category"] for item in filtered.json()["buckets"]]
        self.assertEqual(categories, [CATEGORY_DIRECTORY_FULL])
        self.assertNotIn(THROTTLE_CATEGORY_P2, categories)

    def test_policy_put_validation_echo_and_permissions(self):
        expires = format_utc_z(_future_expires())
        body = {
            "blocked_priorities": ["p2"],
            "throttle_per_hour": {"p1": None, "p2": 20},
            "block_p0_billed": False,
            "expires_at": expires,
        }
        response = self.client.put(self.policy_url(), body, format="json")
        self.assertEqual(response.status_code, 403)

        viewer = create_test_user("source-reader")
        viewer.assign_perms_to_managed_role("authentik_sources_oauth.view_oauthsource")
        self.authenticate(viewer)
        since = format_utc_z(datetime.now(UTC) - timedelta(hours=1))
        self.assertEqual(self.client.get(self.usage_url(), {"since": since}).status_code, 200)
        self.assertEqual(self.client.put(self.policy_url(), body, format="json").status_code, 403)

        self.authenticate(create_test_admin_user())
        missing = self.client.put(
            self.policy_url(),
            {"blocked_priorities": [], "throttle_per_hour": {}, "block_p0_billed": False},
            format="json",
        )
        self.assertEqual(missing.status_code, 400)
        bad_priority = {**body, "blocked_priorities": ["p9"]}
        self.assertEqual(
            self.client.put(self.policy_url(), bad_priority, format="json").status_code, 400
        )
        bad_throttle = {**body, "throttle_per_hour": {"p2": -1}}
        self.assertEqual(
            self.client.put(self.policy_url(), bad_throttle, format="json").status_code, 400
        )

        response = self.client.put(self.policy_url(), body, format="json")
        self.assertEqual(response.status_code, 200)
        echoed = response.json()
        self.assertEqual(echoed["blocked_priorities"], ["p2"])
        self.assertEqual(echoed["throttle_per_hour"], {"p1": None, "p2": 20})
        self.assertFalse(echoed["block_p0_billed"])
        self.assertTrue(echoed["expires_at"].endswith("Z"))
        with self.assertRaises(DingTalkUsagePolicyBlocked):
            check(self.source, CATEGORY_ALLOWLIST)
        prepare_outbound_call(self.source, CATEGORY_AUTH_INFO)

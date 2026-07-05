"""DingTalk managed user service tests."""

from datetime import timedelta

from django.test import TestCase
from django.utils.timezone import now

from authentik.core.tests.utils import create_test_user
from authentik.sources.oauth.dingtalk.managed_users import (
    DingTalkBindingConflict,
    DingTalkManagerNotFound,
    DingTalkSourceUnavailable,
    get_dingtalk_managed_users,
)
from authentik.sources.oauth.dingtalk.selectors import MAX_MANAGER_CHAIN_DEPTH
from authentik.sources.oauth.models import (
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
    UserOAuthSourceConnection,
)


class TestDingTalkManagedUsers(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        self.seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=self.seen,
        )
        self._directory_user("MANAGER", manager_user_id="")

    def _directory_user(
        self,
        user_id: str,
        manager_user_id: str = "MANAGER",
        *,
        active: bool = True,
        is_deleted: bool = False,
    ) -> DingTalkDirectoryUser:
        return DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id=user_id,
            union_id=f"UNION_{user_id}",
            name=user_id,
            manager_user_id=manager_user_id,
            active=active,
            is_deleted=is_deleted,
            dept_id_list=["1"],
            last_seen_at=self.seen,
        )

    def _bind(self, source_user_id: str, *, is_active: bool = True):
        user = create_test_user(source_user_id.lower(), is_active=is_active)
        # Connections are keyed by the stable unionId identity (see get_user_id).
        UserOAuthSourceConnection.objects.create(
            user=user,
            source=self.source,
            identifier=f"UNION_{source_user_id}",
        )
        return user

    def test_returns_recursive_subordinates_with_binding_state(self):
        self._directory_user("EMP1")
        self._directory_user("EMP2", active=False)
        self._directory_user("EMP3", manager_user_id="EMP1")
        self._directory_user("DELETED", is_deleted=True)
        bound = self._bind("EMP1")
        inactive = self._bind("EMP2", is_active=False)

        result = get_dingtalk_managed_users(self.source, "CORP", "MANAGER")

        self.assertEqual(result["source_slug"], "dingtalk")
        self.assertEqual(result["corp_id"], "CORP")
        self.assertEqual(result["manager_user_id"], "MANAGER")
        self.assertEqual(result["resolver"], "dingtalk_manager_chain")
        self.assertFalse(result["stale"])
        self.assertEqual(result["last_synced_at"], self.seen.isoformat())
        self.assertEqual(
            result["diagnostics"],
            {
                "recursion_cycle_detected": False,
                "max_depth_exceeded": False,
                "max_depth_omitted": 0,
            },
        )
        self.assertEqual(
            [item["source_user_id"] for item in result["users"]],
            ["EMP1", "EMP3", "EMP2"],
        )
        self.assertNotIn("DELETED", [item["source_user_id"] for item in result["users"]])
        self.assertEqual(result["users"][0]["source_identifier"], "CORP:EMP1")
        self.assertTrue(result["users"][0]["directory_active"])
        self.assertFalse(result["users"][0]["is_deleted"])
        self.assertEqual(result["users"][0]["authentik_subject"], bound.uid)
        self.assertEqual(result["users"][0]["authentik_subject_type"], "user_uid")
        self.assertTrue(result["users"][0]["authentik_user_active"])
        self.assertEqual(result["users"][0]["binding_status"], "bound")
        self.assertEqual(result["users"][0]["diagnostics"]["authentik_user_pk"], bound.pk)
        self.assertTrue(result["users"][1]["directory_active"])
        self.assertFalse(result["users"][1]["is_deleted"])
        self.assertIsNone(result["users"][1]["authentik_subject"])
        self.assertEqual(result["users"][1]["binding_status"], "unbound")
        self.assertFalse(result["users"][2]["directory_active"])
        self.assertFalse(result["users"][2]["is_deleted"])
        self.assertFalse(result["users"][2]["authentik_user_active"])
        self.assertEqual(result["users"][2]["authentik_subject"], inactive.uid)

    def test_manager_without_subordinates_returns_empty_users(self):
        result = get_dingtalk_managed_users(self.source, "CORP", "MANAGER")

        self.assertEqual(result["users"], [])
        self.assertEqual(
            result["diagnostics"],
            {
                "recursion_cycle_detected": False,
                "max_depth_exceeded": False,
                "max_depth_omitted": 0,
            },
        )

    def test_cycle_sets_diagnostics_without_looping_forever(self):
        self._directory_user("EMP1")
        self._directory_user("EMP2", manager_user_id="EMP1")
        DingTalkDirectoryUser.objects.filter(
            source=self.source,
            corp_id="CORP",
            user_id="MANAGER",
        ).update(manager_user_id="EMP2")

        result = get_dingtalk_managed_users(self.source, "CORP", "MANAGER")

        self.assertEqual(
            [item["source_user_id"] for item in result["users"]],
            ["EMP1", "EMP2"],
        )
        self.assertTrue(result["diagnostics"]["recursion_cycle_detected"])
        self.assertFalse(result["diagnostics"]["max_depth_exceeded"])

    def test_max_depth_sets_diagnostics_when_children_remain(self):
        parent_user_id = "MANAGER"
        for index in range(MAX_MANAGER_CHAIN_DEPTH + 1):
            user_id = f"EMP{index}"
            self._directory_user(user_id, manager_user_id=parent_user_id)
            parent_user_id = user_id

        result = get_dingtalk_managed_users(self.source, "CORP", "MANAGER")

        self.assertEqual(len(result["users"]), MAX_MANAGER_CHAIN_DEPTH)
        self.assertTrue(result["diagnostics"]["max_depth_exceeded"])
        self.assertFalse(result["diagnostics"]["recursion_cycle_detected"])

    def test_missing_manager_is_not_treated_as_empty_scope(self):
        with self.assertRaises(DingTalkManagerNotFound):
            get_dingtalk_managed_users(self.source, "CORP", "MISSING")

    def test_disabled_source_is_unavailable(self):
        self.source.enabled = False
        self.source.save(update_fields=["enabled"])

        with self.assertRaises(DingTalkSourceUnavailable):
            get_dingtalk_managed_users(self.source, "CORP", "MANAGER")

    def test_stale_status_still_returns_cached_subordinates(self):
        DingTalkDirectorySyncStatus.objects.filter(source=self.source, corp_id="CORP").update(
            finished_at=now() - timedelta(hours=25),
        )
        self._directory_user("EMP1")

        result = get_dingtalk_managed_users(self.source, "CORP", "MANAGER")

        self.assertTrue(result["stale"])
        self.assertEqual([item["source_user_id"] for item in result["users"]], ["EMP1"])

    def test_duplicate_source_identifier_fails_closed(self):
        self._directory_user("EMP1")
        self._bind("EMP1")
        other = create_test_user("other-emp1")
        UserOAuthSourceConnection.objects.create(
            user=other,
            source=self.source,
            identifier="UNION_EMP1",
        )

        with self.assertRaises(DingTalkBindingConflict):
            get_dingtalk_managed_users(self.source, "CORP", "MANAGER")

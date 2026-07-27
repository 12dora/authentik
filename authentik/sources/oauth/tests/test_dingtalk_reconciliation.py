"""DingTalk upgrade reconciliation tests."""

from importlib import import_module
from io import StringIO
from json import loads
from pathlib import Path
from types import SimpleNamespace

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase

from authentik.core.tests.utils import create_test_user
from authentik.policies.expression.models import ExpressionPolicy
from authentik.sources.oauth.models import OAuthSource, UserOAuthSourceConnection
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ALLOWLIST_MARKER,
    DINGTALK_DENY_NO_PERMISSION,
    render_dingtalk_allowlist_policy,
)

policy_migration = import_module(
    "authentik.sources.oauth.migrations.0023_reconcile_dingtalk_allowlist_denial_messages"
)

def legacy_policy_body(source_slug: str | None = None, source_pk: str | None = None) -> str:
    """Return a marked but stale policy expression that keeps an old executable body."""
    source_line = f'# source: "{source_slug}"\n' if source_slug else ""
    source_pk_line = f'# source_pk: "{source_pk}"\n' if source_pk else ""
    return (
        f"{DINGTALK_ALLOWLIST_MARKER}\n"
        f"{source_line}"
        f"{source_pk_line}"
        '# config: {"companies":[{"corp_id":"CORP_FAKE","label":"Fake",'
        '"dept_ids":[20,"10"],"allow_all":false}]}\n'
        "return True\n"
    )


class TestDingTalkAllowlistPolicyReconciliation(TestCase):
    """Stored allowlist policies are deterministically reconciled forward."""

    def test_forward_migration_rerenders_old_policy_body(self):
        policy = ExpressionPolicy.objects.create(
            name="dingtalk-allowlist-dt",
            expression=legacy_policy_body("dt", "source-pk"),
        )

        policy_migration.reconcile_dingtalk_allowlist_policies(
            apps, SimpleNamespace(connection=connection)
        )

        policy.refresh_from_db()
        self.assertIn('# source: "dt"', policy.expression)
        self.assertIn('# source_pk: "source-pk"', policy.expression)
        self.assertIn('"dept_ids":["10","20"]', policy.expression)
        self.assertNotIn("other means are not blocked", policy.expression)
        self.assertIn(DINGTALK_DENY_NO_PERMISSION, policy.expression)
        self.assertIn('"missing_session_marker"', policy.expression)
        self.assertIn(
            'request.context.get("authentik/sources/oauth/dingtalk/allowlist")', policy.expression
        )
        self.assertIn("expected_source_pk = 'source-pk'", policy.expression)
        self.assertIn('marker.get("config_hash") == "', policy.expression)
        self.assertNotIn("sign in through DingTalk before accessing this app", policy.expression)

    def test_forward_migration_is_idempotent_and_skips_unparseable_policy(self):
        current = ExpressionPolicy.objects.create(
            name="current",
            expression=render_dingtalk_allowlist_policy(
                {"companies": [{"corp_id": "CORP_FAKE", "allow_all": True}]},
                source_slug="dt",
            ),
        )
        bad = ExpressionPolicy.objects.create(
            name="bad",
            expression=f"{DINGTALK_ALLOWLIST_MARKER}\n# config: {{not-json}}\nreturn True\n",
        )

        policy_migration.reconcile_dingtalk_allowlist_policies(
            apps, SimpleNamespace(connection=connection)
        )
        current_after_first = ExpressionPolicy.objects.get(pk=current.pk).expression
        bad.refresh_from_db()
        policy_migration.reconcile_dingtalk_allowlist_policies(
            apps, SimpleNamespace(connection=connection)
        )

        current.refresh_from_db()
        self.assertEqual(current.expression, current_after_first)
        self.assertEqual(
            bad.expression, f"{DINGTALK_ALLOWLIST_MARKER}\n# config: {{not-json}}\nreturn True\n"
        )

    def test_forward_migration_does_not_widen_string_false_allow_all(self):
        policy = ExpressionPolicy.objects.create(
            name="string-false",
            expression=(
                f"{DINGTALK_ALLOWLIST_MARKER}\n"
                '# config: {"companies":[{"corp_id":"CORP_FAKE","allow_all":"false"}]}\n'
                "return False\n"
            ),
        )

        self.assertIsNone(policy_migration.parse_policy(policy.expression))

        policy_migration.reconcile_dingtalk_allowlist_policies(
            apps, SimpleNamespace(connection=connection)
        )
        policy.refresh_from_db()

        self.assertIn('"allow_all":"false"', policy.expression)
        self.assertNotIn('"allow_all":true', policy.expression)

        out = StringIO()
        call_command("reconcile_dingtalk", stdout=out)
        data = loads(out.getvalue())

        self.assertEqual(data["policies"]["unparseable"][0]["pk"], str(policy.pk))

    def test_management_command_dry_run_check_and_apply_policy_reconciliation(self):
        policy = ExpressionPolicy.objects.create(
            name="dingtalk-allowlist-dt",
            expression=legacy_policy_body("dt"),
        )

        out = StringIO()
        call_command("reconcile_dingtalk", stdout=out)
        data = loads(out.getvalue())

        self.assertEqual(data["policies"]["checked"], 1)
        self.assertEqual(data["policies"]["stale"][0]["pk"], str(policy.pk))
        policy.refresh_from_db()
        self.assertIn("return True\n", policy.expression)

        with self.assertRaises(CommandError):
            call_command("reconcile_dingtalk", "--check", stdout=StringIO())

        out = StringIO()
        call_command("reconcile_dingtalk", "--apply-policies", stdout=out)
        data = loads(out.getvalue())
        self.assertEqual(data["policies"]["updated"][0]["pk"], str(policy.pk))
        policy.refresh_from_db()
        self.assertNotIn("other means are not blocked", policy.expression)
        self.assertIn(DINGTALK_DENY_NO_PERMISSION, policy.expression)
        self.assertIn('"missing_session_marker"', policy.expression)
        self.assertIn("expected_source_pk = ''", policy.expression)
        self.assertNotIn("expected_source_pk = 'None'", policy.expression)


class TestDingTalkIdentityInventory(TestCase):
    """DingTalk SourceConnection inventory reports every ambiguous identity state."""

    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk Test",
            slug="dt",
            provider_type="dingtalk",
            consumer_key="key",
            consumer_secret="secret",
        )
        self.other_source = OAuthSource.objects.create(
            name="DingTalk Other",
            slug="dt-other",
            provider_type="dingtalk",
            consumer_key="key",
            consumer_secret="secret",
        )

    def create_connection(self, name: str, identifier: str, dingtalk: dict, source=None):
        user = create_test_user(name=name, attributes={"dingtalk": dingtalk})
        return UserOAuthSourceConnection.objects.create(
            user=user,
            source=source or self.source,
            identifier=identifier,
        )

    def test_identity_inventory_reports_open_only_missing_conflicts_and_multi_source(self):
        self.create_connection("union-ok", "UNION_OK", {"union_id": "UNION_OK"})
        stale = self.create_connection("stale", "legacy:USER", {"unionId": "UNION_NEW"})
        collision = self.create_connection("collision", "UNION_COLLIDE", {"union_id": "OTHER"})
        collision_target = self.create_connection(
            "collision-target", "legacy:OTHER", {"union_id": "UNION_COLLIDE"}
        )
        self.create_connection("open-only", "legacy:OPEN", {"openId": "OPEN_ONLY"})
        self.create_connection("missing", "legacy:MISSING", {})
        self.create_connection("dupe-a", "DUPLICATE", {"union_id": "DUPLICATE"})
        self.create_connection("dupe-b", "DUPLICATE", {"union_id": "DUPLICATE"})
        self.create_connection(
            "other-source-same-union",
            "UNION_OK",
            {"union_id": "UNION_OK"},
            source=self.other_source,
        )

        out = StringIO()
        call_command("reconcile_dingtalk", stdout=out)
        identities = loads(out.getvalue())["identities"]

        self.assertEqual(identities["checked"], 9)
        self.assertEqual(
            {item["pk"] for item in identities["stale_identifier"]},
            {str(stale.pk), str(collision.pk)},
        )
        self.assertEqual(identities["target_collision"][0]["pk"], str(collision_target.pk))
        self.assertEqual(identities["open_id_only"][0]["open_id"], "OPEN_ONLY")
        self.assertEqual(identities["missing_identity"][0]["identifier"], "legacy:MISSING")
        self.assertEqual(identities["duplicate_identifier"][0]["identifier"], "DUPLICATE")

        with self.assertRaises(CommandError):
            call_command("reconcile_dingtalk", "--check", stdout=StringIO())


class TestDingTalkDocumentationLinks(SimpleTestCase):
    """DingTalk docs do not point at deleted checked-in documents."""

    def test_backtick_doc_paths_exist(self):
        root = Path(__file__).resolve().parents[4]
        docs = [
            root / "docs/dingtalk.md",
        ]
        missing = []
        for doc in docs:
            for path in doc.read_text(encoding="utf-8").split("`"):
                if not path.startswith("docs/"):
                    continue
                if not (root / path).exists():
                    missing.append(f"{doc.relative_to(root)} -> {path}")
        self.assertEqual(missing, [])

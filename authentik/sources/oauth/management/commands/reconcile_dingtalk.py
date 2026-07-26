"""Report and optionally reconcile DingTalk upgrade state."""

from json import dumps, loads

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from authentik.policies.expression.models import ExpressionPolicy
from authentik.sources.oauth.models import OAuthSource, UserOAuthSourceConnection
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_ALLOWLIST_MARKER,
    parse_dingtalk_allowlist_policy,
    render_dingtalk_allowlist_policy,
)


def _dingtalk_policy_source_slug(expression: str) -> str | None:
    return _dingtalk_policy_json_marker(expression, "# source:")


def _dingtalk_policy_source_pk(expression: str) -> str | None:
    return _dingtalk_policy_json_marker(expression, "# source_pk:")


def _dingtalk_policy_json_marker(expression: str, marker: str) -> str | None:
    for raw_line in expression.splitlines():
        line = raw_line.strip()
        if not line.startswith(marker):
            continue
        try:
            value = loads(line.removeprefix(marker).strip())
        except ValueError:
            return ""
        return str(value) if value else ""
    return None


def policy_reconciliation(apply: bool = False) -> dict:
    """Return stale/unparseable managed allowlist policies.

    Update stale policies only when requested.
    """
    result = {"checked": 0, "updated": [], "stale": [], "unparseable": []}
    policies = ExpressionPolicy.objects.filter(expression__contains=DINGTALK_ALLOWLIST_MARKER)
    for policy in policies.order_by("pk"):
        result["checked"] += 1
        config = parse_dingtalk_allowlist_policy(policy.expression)
        if config is None:
            result["unparseable"].append({"pk": str(policy.pk), "name": policy.name})
            continue
        source_slug = _dingtalk_policy_source_slug(policy.expression)
        rendered = render_dingtalk_allowlist_policy(
            config,
            source_slug=source_slug,
            source_pk=_dingtalk_policy_source_pk(policy.expression),
        )
        if rendered == policy.expression:
            continue
        entry = {"pk": str(policy.pk), "name": policy.name, "source_slug": source_slug}
        if apply:
            policy.expression = rendered
            policy.save(update_fields=["expression"])
            result["updated"].append(entry)
        else:
            result["stale"].append(entry)
    return result


def _dingtalk_identity(attributes: dict) -> tuple[str, str]:
    dingtalk = (attributes or {}).get("dingtalk") or {}
    union_id = dingtalk.get("union_id") or dingtalk.get("unionId") or ""
    open_id = dingtalk.get("open_id") or dingtalk.get("openId") or ""
    return str(union_id) if union_id else "", str(open_id) if open_id else ""


def identity_inventory() -> dict:
    """Return deterministic DingTalk SourceConnection identity anomalies."""
    result = {
        "checked": 0,
        "missing_identity": [],
        "open_id_only": [],
        "stale_identifier": [],
        "target_collision": [],
        "duplicate_identifier": [],
    }
    source_ids = list(
        OAuthSource.objects.filter(provider_type="dingtalk").values_list("pk", flat=True)
    )
    if not source_ids:
        return result

    connections = UserOAuthSourceConnection.objects.filter(source_id__in=source_ids).order_by("pk")
    for row in connections.values(
        "pk", "source_id", "source__slug", "identifier", "user__attributes"
    ):
        result["checked"] += 1
        union_id, open_id = _dingtalk_identity(row["user__attributes"])
        base = {
            "pk": str(row["pk"]),
            "source_id": str(row["source_id"]),
            "source_slug": row["source__slug"],
            "identifier": row["identifier"],
            "union_id": union_id,
            "open_id": open_id,
        }
        if not union_id and not open_id:
            result["missing_identity"].append(base)
            continue
        if not union_id and open_id:
            result["open_id_only"].append(base)
            continue
        if row["identifier"] != union_id:
            if (
                UserOAuthSourceConnection.objects.filter(
                    source_id=row["source_id"], identifier=union_id
                )
                .exclude(pk=row["pk"])
                .exists()
            ):
                result["target_collision"].append(base)
            else:
                result["stale_identifier"].append(base)

    duplicate_groups = (
        connections.values("source_id", "source__slug", "identifier")
        .annotate(count=Count("pk"))
        .filter(count__gt=1)
        .order_by("source_id", "identifier")
    )
    for group in duplicate_groups:
        result["duplicate_identifier"].append(
            {
                "source_id": str(group["source_id"]),
                "source_slug": group["source__slug"],
                "identifier": group["identifier"],
                "count": group["count"],
            }
        )
    return result


class Command(BaseCommand):
    """Report and optionally reconcile DingTalk policy/identity upgrade state."""

    help = "Report DingTalk allowlist policy drift and SourceConnection identity anomalies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply-policies",
            action="store_true",
            help="Re-render stale managed DingTalk allowlist policies with the current template.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero when stale policies or identity anomalies are found.",
        )

    def handle(self, *args, **options):
        policy_result = policy_reconciliation(apply=options["apply_policies"])
        identity_result = identity_inventory()
        result = {"policies": policy_result, "identities": identity_result}
        self.stdout.write(dumps(result, ensure_ascii=False, sort_keys=True))
        has_policy_drift = bool(policy_result["stale"] or policy_result["unparseable"])
        has_identity_anomaly = any(
            identity_result[key]
            for key in (
                "missing_identity",
                "open_id_only",
                "stale_identifier",
                "target_collision",
                "duplicate_identifier",
            )
        )
        if options["check"] and (has_policy_drift or has_identity_anomaly):
            raise CommandError("DingTalk reconciliation check found drift or identity anomalies.")

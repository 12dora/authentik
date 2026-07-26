"""Deterministically reconcile stored DingTalk allowlist policy bodies."""

from hashlib import sha256
from json import dumps, loads

from django.db import migrations

DINGTALK_ALLOWLIST_MARKER = "# authentik-managed-dingtalk-allowlist"
DINGTALK_ALLOWLIST_SESSION_KEY = "authentik/sources/oauth/dingtalk/allowlist"
DINGTALK_DENY_RULES_UPDATED = (
    "DingTalk access rules were updated. Sign in with DingTalk again."
)
DINGTALK_DENY_NO_PERMISSION = (
    "You do not have permission to continue. Contact your administrator."
)
DINGTALK_DENY_TEMPORARILY_UNABLE = (
    "We are temporarily unable to verify your DingTalk access. Try again later."
)


def normalize_id_list(value):
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item) for item in value if item is not None})


def normalize_config(config):
    companies = []
    for item in (config or {}).get("companies") or []:
        if not isinstance(item, dict):
            continue
        corp_id = item.get("corp_id") or item.get("corpId")
        if not corp_id:
            continue
        companies.append(
            {
                "allow_all": normalize_allow_all(item.get("allow_all", False)),
                "corp_id": str(corp_id),
                "dept_ids": normalize_id_list(
                    item.get("dept_ids") or item.get("dept_id_list") or item.get("departments")
                ),
                "label": str(item.get("label") or item.get("name") or ""),
            }
        )
    return {"companies": companies}


def normalize_allow_all(value):
    if type(value) is not bool:
        raise ValueError("DingTalk allowlist allow_all must be a boolean.")
    return value


def config_version(config):
    normalized = normalize_config(config)
    authorization_config = {
        "companies": [
            {
                "allow_all": company["allow_all"],
                "corp_id": company["corp_id"],
                "dept_ids": company["dept_ids"],
            }
            for company in normalized["companies"]
        ]
    }
    return dumps(authorization_config, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def parse_policy(expression):
    if DINGTALK_ALLOWLIST_MARKER not in expression:
        return None
    for raw_line in expression.splitlines():
        line = raw_line.strip()
        if not line.startswith("# config:"):
            continue
        try:
            return normalize_config(loads(line.removeprefix("# config:").strip()))
        except ValueError:
            return None
    return {"companies": []}


def policy_source_slug(expression):
    return policy_json_marker(expression, "# source:")


def policy_source_pk(expression):
    return policy_json_marker(expression, "# source_pk:")


def policy_json_marker(expression, marker):
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


def render_policy(config, source_slug=None, source_pk=None):
    normalized = normalize_config(config)
    config_json = dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    config_python = repr(normalized)
    version = config_version(normalized)
    config_hash = sha256(version.encode("utf-8")).hexdigest()
    source_pk_value = str(source_pk) if source_pk else ""
    source_marker = f"# source: {dumps(source_slug)}\n" if source_slug else ""
    source_pk_marker = f"# source_pk: {dumps(source_pk_value)}\n" if source_pk_value else ""
    source_guard = (
        f"""if source and getattr(source, "slug", None) != {source_slug!r}:
    return True
"""
        if source_slug
        else ""
    )
    return f"""{DINGTALK_ALLOWLIST_MARKER}
{source_marker}{source_pk_marker}# config: {config_json}
userinfo = request.context.get("oauth_userinfo") or {{}}
source = request.context.get("source")
{source_guard}corp_id = userinfo.get("corp_id") or userinfo.get("corpId")
dept_values = userinfo.get("dept_id_list") or userinfo.get("deptIdList") or []
if not isinstance(dept_values, (list, tuple, set)):
    dept_ids = []
else:
    dept_ids = sorted({{str(item) for item in dept_values if item is not None}})
def deny(public_message, reason, category):
    ak_logger.warning(
        "dingtalk_allowlist_denied",
        reason=reason,
        public_category=category,
        source_slug=getattr(source, "slug", None),
        corp_id=str(corp_id) if corp_id else "",
        dept_ids=dept_ids,
        request_object=request.obj.__class__.__name__ if request.obj else "",
    )
    ak_message(public_message)
    return False
if not corp_id:
    if userinfo and getattr(source, "provider_type", None) == "dingtalk":
        # A DingTalk source login attempt (userinfo present) that reached policy evaluation
        # without a company id must fail closed instead of silently allowing the login. The
        # ``userinfo`` guard keeps login-button rendering (no userinfo) unaffected.
        return deny(
            {DINGTALK_DENY_TEMPORARILY_UNABLE!r},
            "missing_corp_id",
            "temporarily_unable_to_verify",
        )
    if request.obj.__class__.__name__ != "Application":
        # Another source passing through a shared flow: this allowlist does not apply.
        return True
    if request.user and request.user.is_superuser:
        return True
    marker = request.context.get("{DINGTALK_ALLOWLIST_SESSION_KEY}") or {{}}
    if not marker:
        return deny(
            {DINGTALK_DENY_NO_PERMISSION!r},
            "missing_session_marker",
            "no_permission",
        )
    expected_source_pk = {source_pk_value!r}
    expected_source_slug = {source_slug!r}
    if expected_source_pk and str(marker.get("source_pk") or "") != expected_source_pk:
        return deny(
            {DINGTALK_DENY_NO_PERMISSION!r},
            "source_pk_mismatch",
            "no_permission",
        )
    if expected_source_slug and marker.get("source_slug") != expected_source_slug:
        return deny(
            {DINGTALK_DENY_NO_PERMISSION!r},
            "source_slug_mismatch",
            "no_permission",
        )
    marker_current = (
        marker.get("config_hash") == "{config_hash}"
        or marker.get("config_version") == {version!r}
    )
    if not marker_current:
        return deny(
            {DINGTALK_DENY_RULES_UPDATED!r},
            "config_version_mismatch",
            "rules_updated",
        )
    corp_id = marker.get("corp_id")
    dept_ids = marker.get("dept_ids") or []
    if not isinstance(dept_ids, (list, tuple, set)):
        dept_ids = []
    else:
        dept_ids = sorted({{str(item) for item in dept_ids if item is not None}})
    if not corp_id:
        return deny(
            {DINGTALK_DENY_TEMPORARILY_UNABLE!r},
            "marker_missing_corp_id",
            "temporarily_unable_to_verify",
        )
config = {config_python}
corp_found = False
for company in config.get("companies", []):
    if company.get("corp_id") != str(corp_id):
        continue
    corp_found = True
    if company.get("allow_all"):
        return True
    if set(dept_ids).intersection(company.get("dept_ids") or []):
        return True
if not corp_found:
    return deny(
        {DINGTALK_DENY_NO_PERMISSION!r},
        "corp_not_allowed",
        "no_permission",
    )
return deny(
    {DINGTALK_DENY_NO_PERMISSION!r},
    "department_not_allowed",
    "no_permission",
)
"""


def reconcile_dingtalk_allowlist_policies(apps, schema_editor):
    ExpressionPolicy = apps.get_model("authentik_policies_expression", "ExpressionPolicy")
    db_alias = schema_editor.connection.alias
    policies = ExpressionPolicy.objects.using(db_alias).filter(
        expression__contains=DINGTALK_ALLOWLIST_MARKER
    )
    for policy in policies.order_by("pk"):
        config = parse_policy(policy.expression)
        if config is None:
            continue
        rendered = render_policy(
            config,
            source_slug=policy_source_slug(policy.expression),
            source_pk=policy_source_pk(policy.expression),
        )
        if rendered == policy.expression:
            continue
        ExpressionPolicy.objects.using(db_alias).filter(pk=policy.pk).update(expression=rendered)


class Migration(migrations.Migration):
    dependencies = [
        ("authentik_sources_oauth", "0022_dingtalk_directory_sync_error_contract"),
        ("authentik_policies_expression", "0004_expressionpolicy_authentik_p_policy__fb6feb_idx"),
    ]

    operations = [
        migrations.RunPython(reconcile_dingtalk_allowlist_policies, migrations.RunPython.noop),
    ]

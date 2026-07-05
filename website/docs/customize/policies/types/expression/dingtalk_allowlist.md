---
title: DingTalk multi-company allowlist
tags:
    - policy
    - expression
    - sources
    - dingtalk
---

Use an [expression policy](./index.mdx) to allow DingTalk login only for
selected companies and, when needed, selected departments inside those
companies.

This pattern assumes a single DingTalk OAuth source. Users select their active
company on DingTalk's authorization page, and authentik evaluates the returned
company and department data inside the source authentication and enrollment
flows.

## Contract

- The login page should show one visible DingTalk login entry.
- The DingTalk source slug is `dingtalk`.
- The DingTalk OAuth source requests these scopes:

```text
openid corpid Contact.User.Read
```

- Real `corpId` values and department IDs must live in the database, private
  deployment configuration, or a private operator runbook, not in committed
  documentation.
- Company names in the allowlist are labels only. Access decisions use
  `corpId` and department IDs.
- The policy should be bound to DingTalk-specific source authentication and
  enrollment flows. Flow-level bindings are the panel/default path.
- Stage-binding policies are an advanced manual alternative when the allowlist
  must run at the earliest stage where DingTalk userinfo or mapped attributes
  are available.
- If shared default source flows are used, keep the source slug guard in the
  expression so other sources pass through unchanged.

## Managed policy marker

If the allowlist is managed by an admin UI or automation, keep this marker and
single-line JSON configuration comment at the top of the policy body:

```python
# authentik-managed-dingtalk-allowlist
# config: {"companies":[{"corp_id":"ding_example_corp_a","label":"Example Company A","allow_all":false,"dept_ids":["dept_example_sales","dept_example_hr"]},{"corp_id":"ding_example_corp_b","label":"Example Company B","allow_all":true,"dept_ids":[]}]}
```

The JSON comment is the management source of truth. The executable Python below
must be regenerated from that JSON when the allowlist changes. Manual edits must
update both the `# config:` JSON and the executable `ALLOWLIST`, or use the
DingTalk allowlist panel/API to regenerate both.

## Expression template

When manually configuring the policy, replace the placeholder values in both
the `# config:` JSON and the executable `ALLOWLIST`. Do not commit real tenant
values. Prefer the DingTalk allowlist panel/API when available so both sections
are regenerated together.

```python
# authentik-managed-dingtalk-allowlist
# config: {"companies":[{"corp_id":"ding_example_corp_a","label":"Example Company A","allow_all":false,"dept_ids":["dept_example_sales","dept_example_hr"]},{"corp_id":"ding_example_corp_b","label":"Example Company B","allow_all":true,"dept_ids":[]}]}

# If this policy is reused in shared default source flows, do not affect other
# sources. Dedicated DingTalk flows should still keep this guard.
source = context.get("source")
source_slug = getattr(source, "slug", None) if source else None
if source_slug and source_slug != "dingtalk":
    return True

ALLOWLIST = {
    "ding_example_corp_a": {
        "label": "Example Company A",
        "allow_all": False,
        "dept_ids": {"dept_example_sales", "dept_example_hr"},
    },
    "ding_example_corp_b": {
        "label": "Example Company B",
        "allow_all": True,
        "dept_ids": set(),
    },
}

info = context.get("oauth_userinfo", {}) or {}
prompt_data = context.get("prompt_data", {}) or {}
dingtalk_attrs = prompt_data.get("attributes", {}).get("dingtalk", {}) or {}
if not dingtalk_attrs:
    request_prompt_data = request.context.get("prompt_data", {}) or {}
    dingtalk_attrs = (
        request_prompt_data.get("attributes", {}).get("dingtalk", {}) or {}
    )

corp_id = (
    info.get("corpId")
    or info.get("corp_id")
    or dingtalk_attrs.get("corpId")
    or dingtalk_attrs.get("corp_id")
)

if not corp_id:
    ak_message("钉钉登录失败：无法确认企业信息，请联系管理员。")
    return False

corp_id = str(corp_id)
rule = ALLOWLIST.get(corp_id)
if not rule:
    ak_message("钉钉登录失败：当前企业未被允许，请联系管理员。")
    return False

if rule.get("allow_all"):
    return True

allowed_dept_ids = {
    str(dept_id)
    for dept_id in (rule.get("dept_ids") or set())
    if dept_id is not None
}
if not allowed_dept_ids:
    ak_message("钉钉登录失败：当前部门未被允许，请联系管理员。")
    return False

raw_dept_ids = info.get("dept_id_list")
if raw_dept_ids is None:
    raw_dept_ids = dingtalk_attrs.get("dept_id_list")

# DingTalk department data must be list-like. Strings and scalars are treated
# as invalid/empty so they cannot match by accident.
if isinstance(raw_dept_ids, (list, tuple, set)):
    dept_ids = {str(dept_id) for dept_id in raw_dept_ids if dept_id is not None}
else:
    dept_ids = set()

if not dept_ids:
    ak_message("钉钉登录失败：无法确认部门信息，请联系管理员。")
    return False

if dept_ids & allowed_dept_ids:
    return True

ak_message("钉钉登录失败：当前部门未被允许，请联系管理员。")
return False
```

## Behavior

- `corpId` and `corp_id` are both accepted.
- A DingTalk login (the source callback provides `oauth_userinfo`) that reaches
  policy evaluation without a company ID **fails closed** — it is denied rather
  than silently allowed.
- Another (non-DingTalk) source that passes through a shared flow the policy is
  bound to is **not** blocked by this allowlist: the guard keys off the source in
  context, so only DingTalk logins are gated.
- On the application-access side, a session that carries **no** DingTalk allowlist
  marker (for example a local admin, or a user who authenticated by another
  method) is **not** blocked by this policy — it only re-checks sessions that were
  established through an allowed DingTalk login. The anti-abuse intent (keeping
  non-company DingTalk accounts out) is enforced at login time by the source-link
  guard, not by blocking every non-marker user.
- All company IDs matching the login's corp are scanned, so duplicate rows for the
  same corp (for example one department-scoped and one `allow_all`) all apply.
- Unknown company ID fails closed.
- A company with `allow_all=True` does not require department data.
- A restricted company must have at least one configured `dept_ids` value.
- Missing, string, or scalar `dept_id_list` values are treated as empty and fail
  only when the matched company has department restrictions.
- Department IDs are compared as strings so numeric API values and string
  configuration values match consistently.
- A managed allowlist policy that exists but whose `# config:` line cannot be
  parsed **fails closed** at the source-link guard (and raises a
  `CONFIGURATION_ERROR` event) instead of allowing every DingTalk user. Only a
  genuine absence of allowlist configuration is treated as fail-open.

## Identity and matching mode

- The DingTalk account identity used to match/link authentik users is the stable,
  globally-unique **`unionId`** (returned by the base profile). Do **not** rely on
  the enhancement-dependent `corpId:userid` for identity.
- The authentik **username** is the DingTalk **`userid`** (short, and what
  downstream apps expect). `userid` is unique within a corp but may repeat across
  corps, so a multi-corp deployment can encounter username collisions.
- Configure DingTalk sources with the default **identifier** matching mode. Do
  **not** use `USERNAME_LINK`/`EMAIL_LINK`/`EMAIL_DENY`: DingTalk returns no
  `email_verified` flag and the display name is user-editable, so those modes
  enable account takeover or linking to the wrong account.

## Where to bind it

Create or clone DingTalk-specific source authentication and source enrollment
flows, then bind this policy to those flows through the DingTalk allowlist
panel/API. The source-link guard discovers the same managed policy from the flow
bindings.

Use stage-binding policies only as an advanced manual alternative when an
operator needs enforcement at the earliest stage where
`context["oauth_userinfo"]` or mapped DingTalk attributes are available. The
source-link guard also discovers these stage-binding policies.

Attach both flows to the single DingTalk OAuth source. Do not create one source
per company; the selected company comes from DingTalk's returned `corpId`.

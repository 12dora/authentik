# DingTalk extension

This fork adds DingTalk login controls and a read-only organization directory to
authentik. This document describes the current operating contract. Deployment
records, tenant IDs, test evidence, and incident notes belong outside the
repository.

## What it provides

- One DingTalk OAuth source can accept users from multiple companies.
- A company and department allowlist controls login, enrollment, and source
  linking.
- The same managed policy can protect selected downstream applications.
- Departments, users, and reporting lines are cached locally for API access and
  OIDC or SAML mappings.

DingTalk write operations and live DingTalk calls during token issuance are not
supported.

## Source and identity

The DingTalk source requests `openid`, `corpid`, and `Contact.User.Read`. Keep the
source's default identifier matching mode; email and username linking are unsafe
because DingTalk does not verify email for this integration and `userid` is only
unique inside one company.

Account matching uses `unionId` when available and falls back to `openId`.
The authentik username is DingTalk `userid`, and enrolled DingTalk users are
internal users.

Normalized profile data is stored in `User.attributes["dingtalk"]`. The
source-specific copy is stored under
`User.attributes["dingtalk_sources"][source_pk]`.

## Configure the allowlist

1. Open the DingTalk OAuth source in the admin interface.
2. Open **DingTalk Allowlist**.
3. Discover a company through DingTalk, or enter its `corpId` manually.
4. Allow the whole company or select the permitted departments.
5. Apply the configuration and confirm that the status checks pass.

Use one source for all companies served by the same DingTalk application. Keep
real company IDs, department IDs, secrets, and callback captures out of the
repository.

The allowlist fails closed. With no enabled configuration, a missing company ID,
an unknown company, or a department mismatch denies the DingTalk login. A
full-company rule does not require department data.

The admin interface owns the generated Expression Policy. Do not edit its Python
body by hand.

To require a fresh, allowed DingTalk login for an application, bind the managed
allowlist policy to that application. Superusers remain exempt. When the
allowlist changes, existing DingTalk sessions must sign in again before they can
access a protected application.

## Directory cache

Open **DingTalk Directory** on the source to start a sync and view its status.
Automatic sync runs every two hours for companies found in the allowlist or in
existing DingTalk source connections.

Sync reads DingTalk departments, users, and manager relationships into
source-and-company-scoped cache tables. OIDC and SAML mappings read this cache
instead of calling DingTalk. A successful sync older than 24 hours is reported
as stale.

Removing a company's directory data from the panel deletes that company's cache
and marks its sync status as deleted. It does not change the login allowlist.

## Downstream access

Release DingTalk data only through mappings assigned to the provider that needs
it. Profile values are available from:

```python
dingtalk = request.user.attributes.get("dingtalk", {})
```

Organization context is available from the local cache:

```python
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context

return {
    "dingtalk_org": get_dingtalk_org_context(
        request.user,
        source_slug="dingtalk",
    )
}
```

The organization helper returns company and user IDs, departments, manager
information, and cache freshness. It does not return email, mobile number,
job number, raw profiles, `unionId`, or `openId`.

Directory list APIs require access to the OAuth source plus the matching
`view_dingtalkdirectorydepartment` or `view_dingtalkdirectoryuser` permission.
The user list may return email, mobile number, and job number, so grant its
permission only to approved directory consumers.

## Upgrade check

After an upgrade, check stored policies and source identities:

```bash
ak reconcile_dingtalk --check
```

If only generated policy bodies are stale, update them and check again:

```bash
ak reconcile_dingtalk --apply-policies
ak reconcile_dingtalk --check
```

Identity anomalies are reported but not changed automatically. Resolve them
individually before using directory identity for authorization.

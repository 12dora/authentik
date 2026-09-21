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
A scheduled full refresh runs once a day at 03:{hostname-stable minute} for
companies found in the allowlist or in existing DingTalk source connections.
That job always fetches `user/get` for every user. It is a safety net; live
freshness comes from EasyAuth contact-change events.

For an organization with D=39 departments and U=140 users, a full refresh is
about 220 billed DingTalk calls (`listsub` + `user/list` for every department,
plus one `user/get` per user). An incremental run is about 80 calls: it still
walks every department and user list, but skips `user/get` when the listed row
matches the cache and reuses the stored manager id. The previous two-hourly
schedule mixed those modes and cost about 1,100 calls/day; the daily full
refresh is about 220/day plus EasyAuth's event-driven incrementals.

EasyAuth `POST .../sync/` (body `{"corp_id": ...}`) still defaults to
`"full": true`. Pass `"full": false` for an incremental run. Incremental
requests may include `"user_ids": [...]` — DingTalk userIds mentioned by
contact-change events, max 200 items, each max 128 characters, de-duplicated
server-side — to force a fresh `user/get` for those users even when their
`user/list` row is unchanged. `manager_userid` is only returned by `user/get`.
`user_ids` is allowed only when `full` is false and `corp_id` is given;
otherwise the API returns 400. A listed userId that is not found in the tree
walk is ignored (the walk still tombstones departed users). If a sync for that
(source, corp) is already queued or running, the response is `queued: false`
and those `user_ids` are not recorded.

Manual admin-UI syncs stay full refreshes.

Sync reads DingTalk departments, users, and manager relationships into
source-and-company-scoped cache tables. OIDC and SAML mappings read this cache
instead of calling DingTalk. A successful sync older than 24 hours is reported
as stale.

Removing a company's directory data from the panel deletes that company's cache
and marks its sync status as deleted. It does not change the login allowlist.

## API usage monitoring

Every outbound DingTalk HTTP attempt is counted in an hourly UTC bucket for the
source. Retries count as separate attempts. A failure to write a bucket is
logged and never fails the DingTalk call. The daily directory job deletes
buckets older than 60 days.

EasyAuth pulls the buckets and pushes a short-lived usage policy. A missing or
expired policy allows every call.

Categories and priorities:

- `ak_token` (P0, unbilled): `gettoken`. Never refused.
- `ak_login` (P0, billed): login and allowlist-discovery user calls
  (`userAccessToken`, `contact/users/me`, `getbyunionid`, `user/get`).
  Refused only when `block_p0_billed` is true.
- `ak_auth_info` (P1): `authInfos`.
- `ak_directory_incremental` (P1): directory client calls during an incremental
  sync.
- `ak_directory_full` (P2): directory client calls during a full sync.
- `ak_allowlist` (P2): allowlist department walks.

P1 and P2 are refused when listed in `blocked_priorities`, or when they exceed
`throttle_per_hour` for that priority. A refused call is not sent and is not
retried. A directory sync that hits the policy finishes with
`dingtalk_directory_usage_policy_blocked`. A login refusal is returned as a
login error.

`GET /api/v3/sources/oauth/dingtalk-directory/{slug}/usage/?since=<ISO-8601>`
returns `generated_at` and `buckets` (`hour_start`, `category`, `count`,
`blocked_count`) for `hour_start >= since` truncated to the hour. `since` is
required, must be ISO-8601, and must not be older than 45 days (400 otherwise).
The endpoint uses the same source read permission as directory status.

`PUT /api/v3/sources/oauth/dingtalk-directory/{slug}/usage-policy/` stores
`blocked_priorities`, `throttle_per_hour`, `block_p0_billed`, and `expires_at`
in the shared cache until `expires_at`, persists nothing else, and echoes the
body. It uses the same source change permission as directory sync.

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

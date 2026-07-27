# DingTalk Allowlist Operator Runbook

This runbook covers the DingTalk multi-company allowlist rollout for this
authentik fork. It is intentionally sanitized: do not add real DingTalk
`corpId` values, department IDs, app secrets, access tokens, or session tokens
to this file. Keep real tenant values in the authentik database, private
deployment configuration, or a private operator runbook.

For the sanitized Expression Policy template, see
`website/docs/customize/policies/types/expression/dingtalk_allowlist.md`.

## Phase 0: Contract And Safety Gate

Confirm these points before changing runtime configuration:

- The login page has one visible DingTalk login entry.
- The DingTalk source slug is `dingtalk`.
- The DingTalk source requests exactly these required scopes:

```text
openid corpid Contact.User.Read
```

- The DingTalk login entry uses one OAuth source. Do not create one source per
  company.
- Users select the active company in DingTalk; authentik evaluates the returned
  `corpId`.
- Company names are labels only. Authorization uses `corpId` and department
  IDs.
- Real allowlist values are stored outside committed files.

Stop the rollout if these checks are not true. Fix the source configuration
first so the allowlist does not hide a source or scope problem.

## Runtime Allowlist Contract

The allowlist is a multi-company contract:

- Each company is keyed by normalized `corpId`.
- A company can set `allow_all=True` to allow all users from that company.
- A company can set `allow_all=False` and provide `dept_ids` to allow only
  selected departments.
- `corpId` and `corp_id` are treated as equivalent input names.
- Missing `corpId` fails closed.
- `dept_id_list` must be list-like. Strings and scalars are invalid and treated
  as empty.
- Missing department data fails only when the matched company has department
  restrictions.
- Department IDs are compared as strings.
- A DingTalk source with no enabled allowlist configured fails closed: every
  DingTalk login is denied (with a `CONFIGURATION_ERROR` event) until an
  allowlist is saved and applied. Admin-panel company discovery keeps working
  while logins are denied.

Managed policies must keep this marker and JSON comment at the top of the
Expression Policy body:

```python
# authentik-managed-dingtalk-allowlist
# config: {"companies":[{"corp_id":"ding_example_corp_a","label":"Example Company A","allow_all":false,"dept_ids":["dept_example_sales","dept_example_hr"]}]}
```

The JSON comment is the source of truth for UI or automation. The executable
Python body should be regenerated from it. Manual edits must keep the comment
and Python allowlist in sync.

## Deployment Workflow

1. Create or clone a DingTalk-specific source authentication flow.
2. Create or clone a DingTalk-specific source enrollment flow.
3. Open the native DingTalk allowlist Admin UI panel or API for the source.
4. Save one managed Expression Policy from the panel/API.
5. Bind the managed policy to the DingTalk source authentication flow.
6. Bind the same managed policy to the DingTalk source enrollment flow.
7. Attach both flows to the single DingTalk OAuth source with slug `dingtalk`.
8. Confirm the login page still shows one DingTalk entry.

The panel/API uses flow-level bindings as the standard local/admin path. This
keeps the allowlist attached to the source authentication and enrollment flows
that receive DingTalk callback data.

DingTalk Open Platform should keep using the standard authentik DingTalk
callback URL, `/source/oauth/callback/<source-slug>/`. The allowlist discovery
flow uses a signed discovery `state`; the DingTalk-specific callback detects
that state and returns the admin popup result without authenticating, enrolling,
or linking a user.

Use stage-binding policies only as an advanced manual alternative when an
operator needs the allowlist to run at the earliest stage where DingTalk
userinfo or mapped attributes are available.

If the environment uses shared default source flows, keep the source slug guard
in the Expression Policy:

```python
source = context.get("source")
source_slug = getattr(source, "slug", None) if source else None
if source_slug and source_slug != "dingtalk":
    return True
```

Dedicated DingTalk flows are still preferred because they reduce the blast
radius of an allowlist edit.

## Admin UI/API Allowlist Panel

Use the native DingTalk allowlist panel/API instead of editing the policy body
by hand:

1. Open the OAuth source detail page for source slug `dingtalk`.
2. Open the DingTalk allowlist panel.
3. Use discovery to load the selected company and department tree when DingTalk
   OpenAPI permissions allow it.
4. If discovery fails or department permissions are unavailable, use manual
   entry with values from private DingTalk admin/API output.
5. Add or edit company labels for operator readability.
6. Add one or more department IDs for restricted companies.
7. Toggle `allow_all` only when every user in that DingTalk company should be
   allowed.
8. Save and apply once.
9. Confirm the panel reports that the managed policy exists, both flow bindings
   are enabled, and the single DingTalk source is attached to the expected
   flows.

When code changes alter the generated Expression Policy body, rebuilding and
recreating the Docker containers is not enough to update existing database
rows. Save and apply the allowlist again from the panel/API, or re-render the
managed policy with an operational script, so the stored `ExpressionPolicy`
body contains the newest logic.

Discovery is an admin convenience, not a login-time dependency. Manual entry is
the fallback when DingTalk discovery or department permissions are unavailable,
and a failed department fetch should not prevent saving a manually entered
allowlist.

## Common Operator Changes

Add a company:

1. Get the real `corpId` from a private DingTalk callback capture, existing user
   attributes, or DingTalk admin/API output.
2. Add one company entry with a human-readable label.
3. Choose either `allow_all=True` or department restrictions.
4. Save and apply the managed policy once.
5. Test one allowed and one denied path before announcing availability.

Add multiple departments:

1. Keep the same company entry.
2. Add every allowed department ID to `dept_ids`.
3. Leave `allow_all=False`.
4. Save and apply.
5. Test a user in one allowed department and a user outside the list.

Toggle `allow_all`:

1. Set `allow_all=True` only after approval from the data or tenant owner.
2. Keep `dept_ids` empty or ignored for that company.
3. Save and apply.
4. Test that a user without department data can still log in for that company.

Do not create additional DingTalk login sources for these changes.

## Source-Link Guard

Normal unauthenticated login and enrollment are covered by the flow-bound
allowlist. Already-authenticated source linking must also be guarded so a
disallowed DingTalk identity cannot create or update a
`UserOAuthSourceConnection`.

The allowlist API still returns two compatibility aliases for older callers:
status responses include both `source_link_guard.enabled` and the legacy scalar
`sourceLinkGuard`, and discovery-start responses include both
`authorization_url` and `url`. Treat the snake-case object and
`authorization_url` as canonical for new callers. Do not remove the aliases
until external consumers have been inventoried, schema/client generation has
been coordinated, and a deprecation window has elapsed.

The source-link guard discovers the managed allowlist from flow-level bindings
and from advanced stage-binding policies, so it stays aligned with both the
panel/default path and manual earliest-stage deployments.

Production rollout must satisfy one of these conditions:

- A DingTalk-specific source-link guard denies disallowed `corpId` or department
  data before a source connection is saved.
- User-initiated DingTalk source linking is disabled or hidden, and direct link
  attempts are tested to prove a disallowed connection is not persisted.

Do not treat the allowlist as production-ready until source-link behavior has
been validated.

## Downstream Application Guard

The DingTalk allowlist has two enforcement layers:

1. DingTalk OAuth callback denies non-allowlisted `corpId` or department data
   before a login, enrollment, or source-link result is accepted.
2. Protected downstream applications bind the same managed DingTalk allowlist
   Expression Policy to their `Application` object. In that context the policy
   requires a current browser session marker that was written by a successful
   DingTalk allowlist login.

The session marker is server-side Django session data under:

```text
authentik/sources/oauth/dingtalk/allowlist
```

It includes the DingTalk source slug, source identifier, `corp_id`, department
IDs, DingTalk user identifiers, the authenticated authentik user primary key,
`checked_at`, and a stable allowlist `config_hash`. Application policy
evaluation injects this marker only for `Application` targets. This keeps
source settings, user settings, and unrelated policy surfaces from receiving
OAuth-only DingTalk state.

When the managed allowlist policy is bound to an `Application`, the policy:

- allows superusers without a DingTalk marker, so local admin accounts such as
  `akadmin` keep seeing and reaching protected applications (including the
  `/if/user/` application library) after a password login;
- denies non-superuser password logins, other social logins, and stale sessions
  that do not have the DingTalk marker;
- denies markers from an older allowlist config hash, requiring the user to log
  in through DingTalk again after an allowlist change;
- re-checks the marker `corp_id` and department IDs against the current policy
  config instead of trusting a stored `allowed=True` flag.

Managed policy bodies stored in the database are executable authorization text.
Migration `0015_rerender_dingtalk_allowlist_policies` was a no-op in released
trees and did not update already stored policy bodies. Current upgrades use the
later forward reconciliation migration plus the audit command below to make the
postcondition explicit:

```bash
ak reconcile_dingtalk --check
```

If the command reports policy drift after code changes that alter the generated
policy body, apply the current renderer explicitly:

```bash
ak reconcile_dingtalk --apply-policies
ak reconcile_dingtalk --check
```

Re-saving from the panel should produce an equivalent policy body, but it is not
the upgrade mechanism and should not be the only evidence that a deployment was
reconciled.

Bind the managed policy only to applications that require DingTalk organization
membership, such as the EasyAuth Portal application. Do not bind it globally to
every application by default: that can lock operators out of admin, user
settings, recovery, device, or internal applications. If the same downstream
resource is exposed through multiple Authentik applications or providers, bind
the policy to every entry point for that resource.

For the local EasyAuth deployment, the current runtime protection path is:

- run `ak reconcile_dingtalk --check` after deploying code that changes the
  generated policy body, and apply policy reconciliation if it reports drift;
- bind that managed policy to the `EasyAuth Portal` `Application`;
- remove obsolete OAuth source-level allowlist bindings so user settings and
  source listing views are not filtered by OAuth callback-only state;
- keep authentication and enrollment flow bindings enabled for DingTalk login.

Before claiming a local image is the code under review, confirm the image
revision label matches the expected commit:

```bash
docker image inspect authentik-dingtalk:local \
  --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
```

Refresh-token behavior is intentionally not fail-closed on the browser session
marker. OAuth2 refresh requests often arrive without the browser session, and
the upstream Authentik model keeps refresh tokens alive even after a session is
terminated. Treat `offline_access` as a separate risk decision for protected
applications: either do not grant offline access to these apps, or add a later
token/grant-level revocation design that persists DingTalk allowlist metadata
with the grant and revokes it when the allowlist changes.

## User Type For DingTalk Logins

DingTalk logins represent company employees, so the DingTalk source now creates
enrolled users as `internal` users, and an allowed DingTalk login promotes an
existing `external` linked user to `internal`. Without this, users enrolled via
the default source enrollment flow default to `external` and are rejected at
`/if/user/` with "Interface can only be accessed by internal users." whenever
the brand has no default application. A denied DingTalk login never changes the
linked user's type.

## Wrong Source Connection Cleanup

If a DingTalk identifier was accidentally connected to the wrong user, such as
`akadmin`, fix the data after deploying the guard:

1. Find the DingTalk OAuth source by slug, normally `dingtalk`.
2. Find `UserOAuthSourceConnection` rows for that source whose `user` is the
   wrong account.
3. Delete only the incorrect DingTalk connection row. Do not delete the user or
   the DingTalk source.
4. Log out of Authentik, start a fresh DingTalk login, and confirm the source
   connection is created for the intended user.

This cleanup should be done with a private operational command or Django shell
using real identifiers from the tenant database. Do not paste real identifiers,
tokens, or secrets into committed docs.

## Validation Checklist

Run these checks after deployment and after each allowlist change:

- Allowed company with matching department can log in.
- Allowed company with `allow_all=True` can log in without department data.
- Rejected company is denied.
- Allowed company with rejected department is denied.
- Missing `corpId` is denied.
- Missing, string, or scalar `dept_id_list` is denied only when department
  restrictions exist.
- Source-link attempt with no allowed DingTalk connection is denied and does
  not create or update a source connection.
- A password login or other social login cannot access a DingTalk-protected
  downstream application.
- A successful allowlisted DingTalk login can access the protected downstream
  application.
- Updating the allowlist config hash makes old DingTalk session markers fail and
  requires a fresh DingTalk login.
- The login page still shows one DingTalk entry.
- A no-cache request to the reverse-proxied admin asset contains the newest
  DingTalk allowlist policy code after rebuild and container recreation.
- Downstream mappings still receive the expected attributes for allowed users:

```text
dingtalk.corp_id
dingtalk.user_id
dingtalk.union_id
dingtalk.name
dingtalk.avatar
dingtalk.title
dingtalk.dept_id_list
```

Downstream applications should continue to receive DingTalk data only through
explicit per-application OIDC or SAML mappings.

## Rollback

Use the smallest rollback that restores access safely:

1. Disable the DingTalk allowlist policy bindings on the DingTalk source
   authentication and enrollment flows.
2. Restore the DingTalk source to the previous authentication and enrollment
   flows if the cloned flows are causing issues.
3. Keep the single DingTalk login source attached unless DingTalk login itself
   must be hidden.
4. Hide or detach the DingTalk source only when the business decision is to
   disable DingTalk login entirely.

After rollback, verify that there is still only one DingTalk source entry if
DingTalk remains visible.

## Tenant Validation TODOs

These checks require a real DingTalk tenant and must be tracked in the private
deployment record:

- Verify that DingTalk returns the selected company's `corpId` for a user with
  multiple companies.
- Verify department IDs from DingTalk admin/API output match
  `dingtalk.dept_id_list` values stored on authentik users.
- Verify denial messages are acceptable to operators and end users.
- Verify source-link denial with a real disallowed DingTalk identity.

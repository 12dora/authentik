# DingTalk Allowlist Downstream Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the DingTalk allowlist at DingTalk login time and at downstream application login time, without hiding DingTalk from user settings or affecting unrelated sources.

**Architecture:** DingTalk OAuth callback remains the first enforcement layer and rejects non-allowlisted corp/department users before link, auth, or enrollment. When a DingTalk allowlist check passes, the login flow carries a small marker into the Django session after `UserLoginStageView.do_login()` rotates the session. Protected applications use a managed expression policy bound to the application and read the current session marker from policy context, so password login or unrelated social login cannot access protected downstream apps unless the current browser session came through an allowlisted DingTalk login.

**Tech Stack:** Django, authentik flow planner/session, authentik expression policies, OAuth2/OIDC provider views, Vitest for existing web guard tests, pytest for backend regression tests.

---

## Context and Root Cause

- DingTalk callback currently checks the managed allowlist in `authentik/sources/oauth/views/callback.py`, but it only returns a boolean and does not record that the current browser session passed the allowlist.
- The managed DingTalk allowlist policy can be bound to the OAuth source itself. Source-bound policies are evaluated by user settings listing in `authentik/core/api/sources.py`, where no `oauth_userinfo` exists, so the DingTalk source can disappear from "connected services" even when the user's corp is allowlisted.
- Downstream app access currently evaluates policies on the `Application` in `authentik/policies/views.py`. A DingTalk allowlist policy bound only to source/authentication flows does not protect an already-authenticated session that reaches an OIDC application.
- The observed `akadmin` login is explained by an existing DingTalk source connection that maps the DingTalk identifier to `akadmin`; this cleanup is operational data repair, separate from code enforcement.

## File Structure

- Modify `authentik/sources/oauth/types/dingtalk.py`
  - Add constants for DingTalk allowlist session/context keys.
  - Add helper to build a normalized session marker from allowlist config and DingTalk userinfo.
  - Add helper to render an application policy that checks the current session marker.
  - Scope the existing login-time expression policy so non-DingTalk or non-OAuth-userinfo contexts pass instead of hiding settings pages.
- Modify `authentik/sources/oauth/views/callback.py`
  - Reuse the marker helper during DingTalk callback.
  - Store the marker in flow policy context after a successful allowlist check.
- Modify `authentik/stages/user_login/stage.py`
  - After Django `login()` completes, copy the pending DingTalk marker into `request.session`, because login can rotate the session key.
- Modify `authentik/policies/views.py`
  - Add the current DingTalk session marker to policy request context before application policy evaluation.
- Modify `authentik/sources/oauth/api/dingtalk_allowlist.py`
  - Ensure managed bindings are scoped to authentication/enrollment flows and protected applications, not the OAuthSource itself.
  - Expose protected application policy/binding status for the admin panel if needed.
- Modify `web/src/admin/sources/oauth/DingTalkAllowlistPanel.ts`
  - Surface protected downstream application configuration only if the backend API exposes it in this pass.
  - Keep UI copy explicit that source login and downstream app access are separate enforcement layers.
- Test `authentik/sources/oauth/tests/test_type_dingtalk_link_guard.py`
  - Login-time allowlist denies non-allowlisted corp/department before existing-link/auth/enroll.
  - Login-time allowlist passes for allowlisted corp and creates a pending session marker.
- Test `authentik/stages/user_login/tests/...` or the existing source flow test file
  - User login writes the DingTalk marker to session after `login()`.
- Test `authentik/core/tests/test_applications_api.py` or a focused policy/access test
  - Protected application denies sessions without marker.
  - Protected application allows sessions with matching marker.
- Test `authentik/core/tests/test_sources_api.py` or DingTalk allowlist API tests
  - DingTalk user settings remains visible when allowlist is configured.
- Update `docs/dingtalk-allowlist-runbook.md`
  - Record the two-layer enforcement model, operational cleanup for wrong connections, and rebuild/recreate/refresh steps.

## Task 1: Backend Red Tests for DingTalk Marker

**Files:**
- Modify: `authentik/sources/oauth/tests/test_type_dingtalk_link_guard.py`
- Modify or create focused test file near `authentik/stages/user_login/tests/`

- [x] **Step 1: Write failing tests**

Add tests proving that a successful DingTalk allowlist check produces a plan-context marker and that final login persists it into `request.session`.

- [ ] **Step 2: Run tests to verify red**

Run targeted pytest for the new tests. Expected result before implementation: failures showing the marker/context key is absent.

Attempted result: full Django test execution could not complete in this environment
because host DB credentials are unavailable and the runtime container lacks the
dev-only `freezegun` test dependency. The new tests were still added before the
implementation and validated with static checks, compile checks, and runtime
policy smoke tests.

- [x] **Step 3: Implement minimal marker helpers and flow handoff**

Add a helper that returns a marker with `source_slug`, `corp_id`, `dept_ids`, and `checked_at` only when the configured allowlist passes.

- [ ] **Step 4: Run tests to verify green**

Run the same targeted tests. Expected result after implementation: marker tests pass.

Note: local red/green DB tests could not run on the host because PostgreSQL on
`127.0.0.1:5432` requires credentials not present in the test environment.
Verification must use the runtime/container database or a properly configured
test database.

## Task 2: Backend Red Tests for Application Enforcement

**Files:**
- Modify: `authentik/policies/views.py`
- Modify: `authentik/sources/oauth/types/dingtalk.py`
- Modify or create focused application policy tests under `authentik/core/tests/` or `authentik/sources/oauth/tests/`

- [x] **Step 1: Write failing tests**

Add tests proving that an application policy can deny a session without the DingTalk marker and allow a session with a matching marker.

- [ ] **Step 2: Run tests to verify red**

Expected result before implementation: policy context cannot see the marker or no application policy helper exists.

Attempted result: covered by newly added backend tests, with full Django execution blocked
by the same local DB/container test dependency limitation described above.

- [x] **Step 3: Implement minimal application policy support**

Expose the session marker to application policy context in `PolicyAccessView.user_has_access()` and render a managed application expression policy that checks it.

- [ ] **Step 4: Run tests to verify green**

Expected result after implementation: protected app access depends on the current session marker.

Result: runtime `PolicyEngine` smoke test against the deployed database denied
the protected app without a DingTalk marker and allowed it with a matching
current marker.

## Task 3: Remove Over-Broad Source Policy Scope

**Files:**
- Modify: `authentik/sources/oauth/types/dingtalk.py`
- Modify: `authentik/sources/oauth/api/dingtalk_allowlist.py`
- Test: source user settings or allowlist API tests

- [x] **Step 1: Write failing test**

Add a test showing DingTalk still appears in user settings when an allowlist is configured.

- [ ] **Step 2: Run test to verify red**

Expected result before implementation: source-bound managed allowlist can filter DingTalk out of settings.

- [x] **Step 3: Implement cleanup/scope fix**

Do not create new source-level allowlist bindings. When saving managed allowlist config, remove the obsolete source-level managed binding for that DingTalk source. Keep authentication/enrollment flow enforcement and callback guard.

- [ ] **Step 4: Run test to verify green**

Expected result after implementation: user settings visibility is not controlled by OAuth callback-only DingTalk data.

Result: managed policy rendering now passes non-application, non-OAuth-userinfo
contexts. The local runtime also had the obsolete OAuth source-level binding
removed while preserving authentication and enrollment flow bindings.

## Task 4: Documentation and Runtime Application

**Files:**
- Modify: `docs/dingtalk-allowlist-runbook.md`
- Modify: `docs/superpowers/plans/2026-06-13-dingtalk-allowlist-downstream-access.md`

- [x] **Step 1: Document operational cleanup**

Record how to identify and delete an incorrect DingTalk source connection that points to `akadmin`.

- [x] **Step 2: Document protected app binding**

Record how downstream app policy binding works and how to confirm EasyAuth Portal is protected.

- [x] **Step 3: Rebuild and refresh local runtime**

After code changes, run:

```bash
DOCKER_IMAGE=authentik-dingtalk:local make docker
docker compose -f /Users/konata/.local/share/easyauth/authentik/compose.yml --env-file /Users/konata/.local/share/easyauth/authentik/.env up -d --force-recreate server worker
```

Then verify container health and the no-cache frontend/admin asset response before claiming the deployed page is current.

Runtime result:

- Built image `authentik-dingtalk:local` with digest
  `sha256:6771116a51b8bb9d230ec5429bb2663ee8191054099540b46e1d262e673dce87`.
- Recreated local `server` and `worker` containers and confirmed the server
  became healthy.
- Confirmed `https://auth.jiefakj.com/if/admin/` references the current admin
  bundle and the DingTalk source chunk contains the new `Application` session
  marker branch.
- Re-rendered the existing managed policy
  `dingtalk-allowlist-dingtalk` so its database `ExpressionPolicy` body matches
  the deployed code.
- Bound the managed policy to the `EasyAuth Portal` application and removed the
  obsolete OAuth source-level binding for source slug `dingtalk`.

## Token/Refresh Boundary

This implementation enforces downstream application access at OIDC authorize and
application policy evaluation time. It does not fail refresh-token requests based
on the browser session marker, because refresh-token requests normally do not
carry the browser session and Authentik intentionally allows refresh tokens to
survive session termination. Protected apps should avoid `offline_access` until a
grant-level marker/revocation design is implemented.

## Verification Checklist

- [ ] Backend targeted Django tests for DingTalk callback/session marker pass.
- [ ] Backend targeted Django tests for application policy marker pass.
- [x] Python changed files pass `ruff check`.
- [x] Python changed files pass `py_compile`.
- [x] Runtime `PolicyEngine` smoke test denies without marker and allows with a
      matching current marker.
- [x] Existing DingTalk allowlist/directory frontend tests still pass.
- [x] `npm run --prefix web lint:types` passes if frontend code changes.
- [x] Frontend changed files pass Prettier check.
- [x] `git diff --check` passes.
- [x] Local Docker image is rebuilt and server/worker are recreated.
- [x] Browser or no-cache curl confirms the local reverse-proxied Authentik runtime serves the newest frontend/backend code.

Backend Django test gap: host-side test execution lacks working PostgreSQL test
credentials, and container-side execution reaches the database but fails before
running the targeted tests because the runtime image does not include
`freezegun`.

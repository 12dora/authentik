# DingTalk Directory And Org Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a controlled DingTalk directory cache and organization relationship service so downstream applications can receive current-user DingTalk organization context and, when explicitly permitted, query directory data without integrating with DingTalk directly.

**Architecture:** Keep DingTalk as one OAuth source and reuse its server-side app credentials for directory sync. Store DingTalk departments, users, and manager relationships in local cache tables under the `authentik_sources_oauth` app, compute organization context from those tables, and expose data through explicit OIDC/SAML mappings or narrow API endpoints. Do not proxy every downstream request to DingTalk, do not expose raw DingTalk profiles by default, and do not add DingTalk-specific fields to generic authentik core models.

**Tech Stack:** Django models and migrations, DRF serializers/viewsets, Dramatiq actors and tenant schedules, existing OAuthSource/DingTalk client code, authentik property mappings, requests-mock, pytest, Lit/PatternFly admin UI.

---

## Current Boundary

Existing DingTalk support can provide these current-user attributes after login:

```text
dingtalk.corp_id
dingtalk.user_id
dingtalk.union_id
dingtalk.name
dingtalk.avatar
dingtalk.title
dingtalk.dept_id_list
dingtalk.job_number
dingtalk.role_list
```

That is enough for downstream applications that only need identity, department
IDs, title, avatar, or roles for the currently logged-in user. Those applications
should continue to use per-application OIDC `ScopeMapping` or SAML
`SAMLPropertyMapping` objects.

This plan adds the missing capabilities:

- Local department cache with names, parent IDs, and department paths.
- Local user directory cache scoped by DingTalk source and `corp_id`.
- Direct manager and manager-chain lookup from cached `manager_userid`.
- Safe current-user organization context claims.
- Optional read API for applications that are explicitly allowed to query
  directory data.

This plan does not add:

- Writeback to DingTalk.
- Real-time DingTalk proxying on every token or API request.
- Global release of full DingTalk profiles to every downstream application.
- Cross-source identity merging beyond `source + corp_id + user_id`.

## External DingTalk API References

Use the official DingTalk documentation as the implementation reference:

- Query user details: `https://open.dingtalk.com/document/development/query-user-details`
- Query department user details: `https://open.dingtalk.com/document/development/queries-the-complete-information-of-a-department-user`
- Query department list: `https://open.dingtalk.com/document/development/user-management-acquires-the-list-departments`

Existing code already uses these legacy OpenAPI endpoints:

```text
GET  https://oapi.dingtalk.com/gettoken
POST https://oapi.dingtalk.com/topapi/v2/department/listsub
POST https://oapi.dingtalk.com/topapi/v2/user/get
```

The directory sync should additionally use the department user-detail list
endpoint for full user discovery. The implementation must keep endpoint
constants in one DingTalk-specific module and test request shapes with
`requests-mock`.

## Product Decision

Recommended rollout shape:

1. Authentik becomes the single server-side DingTalk integration for login,
   directory sync, and organization relationship cache.
2. Downstream applications receive only the fields they need.
3. For current-user data, prefer OIDC/SAML claims.
4. For directory lookup, expose a narrow API with authentik RBAC and application
   ownership review.
5. Downstream applications should connect to DingTalk directly only when they
   require write operations, sub-minute freshness, or DingTalk APIs outside this
   read-only directory/org scope.

## Data Contract

The computed organization context returned to OIDC/SAML mappings must be
JSON-safe and stable:

```python
{
    "corp_id": "ding_corp_id",
    "user_id": "manager4220",
    "source_slug": "dingtalk",
    "departments": [
        {
            "dept_id": "1",
            "name": "总部",
            "parent_id": "",
            "path": [
                {"dept_id": "1", "name": "总部"},
            ],
        }
    ],
    "manager": {
        "user_id": "manager1001",
        "name": "Alice Manager",
        "title": "Engineering Director",
        "avatar": "https://example.invalid/avatar.png",
    },
    "manager_chain": [
        {
            "user_id": "manager1001",
            "name": "Alice Manager",
            "title": "Engineering Director",
            "avatar": "https://example.invalid/avatar.png",
        }
    ],
    "stale": False,
    "last_synced_at": "2026-06-11T09:00:00Z",
}
```

Privacy rule:

- Default current-user claims may include department names and manager chain.
- Default current-user claims must not include mobile, email, raw profile, or
  every user in the department.
- API endpoints that return directory users may expose mobile/email only when
  the endpoint permission and serializer explicitly include them.

## File Structure

Create:

- `authentik/sources/oauth/dingtalk/__init__.py`: DingTalk directory package marker.
- `authentik/sources/oauth/dingtalk/client.py`: reusable DingTalk app-token, department, department-user-list, and user-detail client functions.
- `authentik/sources/oauth/dingtalk/sync.py`: sync orchestration, normalization, upsert logic, deletion marking, and sync counters.
- `authentik/sources/oauth/dingtalk/selectors.py`: read-only helpers for current-user organization context, department paths, manager chain, and staleness.
- `authentik/sources/oauth/api/dingtalk_directory.py`: admin sync endpoints and optional downstream read endpoints.
- `authentik/sources/oauth/tests/test_dingtalk_directory_client.py`: API client request/normalization tests.
- `authentik/sources/oauth/tests/test_dingtalk_directory_sync.py`: model upsert, delete marking, manager-chain, and stale-data tests.
- `authentik/sources/oauth/tests/test_api_dingtalk_directory.py`: permission and response contract tests.
- `web/src/admin/sources/oauth/DingTalkDirectoryPanel.ts`: source detail panel for sync status and manual sync.
- `web/test/unit/dingtalk-directory-panel.test.ts`: frontend state/render tests.
- `docs/dingtalk-directory-org-service.md`: operator and downstream integration runbook.

Modify:

- `authentik/sources/oauth/models.py`: add DingTalk directory cache models.
- `authentik/sources/oauth/migrations/00xx_dingtalk_directory.py`: add cache tables and indexes.
- `authentik/sources/oauth/tasks.py`: add scheduled and manual directory sync actors.
- `authentik/sources/oauth/apps.py`: add DingTalk directory tenant schedule.
- `authentik/sources/oauth/urls.py`: register directory API routes.
- `web/src/admin/sources/oauth/OAuthSourceViewPage.ts`: render the DingTalk directory panel only for DingTalk sources.
- `docs/dingtalk-oauth-downstream-mappings.md`: document org-context claim mappings and when to use the directory API.
- `schema.yml`, `blueprints/schema.json`, and generated clients: regenerate after adding API serializers/routes.

Do not modify for the MVP:

- `authentik/core/models.py`
- `authentik/core/expression/evaluator.py`
- `authentik/sources/oauth/views/callback.py`
- `authentik/sources/oauth/views/redirect.py`
- `authentik/sources/oauth/clients/oauth2.py`
- `web/src/admin/sources/oauth/OAuthSourceForm.ts`

If expression imports prove unavailable in this environment, add a second-stage
global helper only after a failing mapping test proves that direct imports do not
work. Keep that fallback in a separate commit.

## Data Model

Add these models to `authentik/sources/oauth/models.py`.

```python
class DingTalkDirectorySyncStatus(InternallyManagedMixin, SerializerModel):
    source = models.ForeignKey("OAuthSource", on_delete=models.CASCADE)
    corp_id = models.TextField()
    status = models.TextField(default="unknown")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    counters = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = (("source", "corp_id"),)
        indexes = [
            models.Index(fields=["source", "corp_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["finished_at"]),
        ]


class DingTalkDirectoryDepartment(InternallyManagedMixin, SerializerModel):
    source = models.ForeignKey("OAuthSource", on_delete=models.CASCADE)
    corp_id = models.TextField()
    dept_id = models.TextField()
    name = models.TextField(blank=True, default="")
    parent_dept_id = models.TextField(blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)
    is_deleted = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField()
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("source", "corp_id", "dept_id"),)
        indexes = [
            models.Index(fields=["source", "corp_id", "dept_id"]),
            models.Index(fields=["source", "corp_id", "parent_dept_id"]),
            models.Index(fields=["is_deleted"]),
        ]


class DingTalkDirectoryUser(InternallyManagedMixin, SerializerModel):
    source = models.ForeignKey("OAuthSource", on_delete=models.CASCADE)
    corp_id = models.TextField()
    user_id = models.TextField()
    union_id = models.TextField(blank=True, default="")
    open_id = models.TextField(blank=True, default="")
    name = models.TextField(blank=True, default="")
    avatar = models.TextField(blank=True, default="")
    title = models.TextField(blank=True, default="")
    email = models.TextField(blank=True, default="")
    mobile = models.TextField(blank=True, default="")
    job_number = models.TextField(blank=True, default="")
    manager_user_id = models.TextField(blank=True, default="")
    dept_id_list = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    raw = models.JSONField(default=dict, blank=True)
    is_deleted = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField()
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("source", "corp_id", "user_id"),)
        indexes = [
            models.Index(fields=["source", "corp_id", "user_id"]),
            models.Index(fields=["source", "corp_id", "manager_user_id"]),
            models.Index(fields=["source", "corp_id", "is_deleted"]),
        ]
```

Model policy:

- `manager_user_id` is optional. Missing manager data must not fail login, sync,
  or token issuance.
- `dept_id_list` stores strings only, even when DingTalk returns numbers.
- `raw` is for troubleshooting and admin API only. Do not release it in default
  OIDC/SAML mappings.
- `is_deleted=True` marks objects absent from the latest successful sync. Do not
  hard-delete on the first missing observation.

## Sync Algorithm

Directory sync runs per `(source, corp_id)`:

1. Acquire a lock using a cache key derived from source primary key and corp ID.
2. Fetch app access token from DingTalk using the OAuth source `consumer_key` and
   `consumer_secret`.
3. Traverse departments from root department `1` with bounded depth and count.
4. Upsert every department with `last_seen_at = sync_started_at`.
5. For every active department, fetch department user details with pagination.
6. Normalize every user and upsert by `(source, corp_id, user_id)`.
7. Store `manager_userid` as `manager_user_id` when returned.
8. Mark departments and users not seen during this sync as `is_deleted=True`.
9. Update `DingTalkDirectorySyncStatus` with counters and finish time.
10. On DingTalk or network error, keep existing cache and record the error.

Sync freshness:

- Default scheduled sync cadence: every 2 hours, aligned with LDAP source sync.
- Org-context selectors mark data as stale when latest successful sync is older
  than 24 hours.
- Token issuance must not call DingTalk live.

## API Contract

Admin/source endpoints:

```text
GET  /api/v3/sources/oauth/dingtalk-directory/<source_slug>/status/
POST /api/v3/sources/oauth/dingtalk-directory/<source_slug>/sync/
GET  /api/v3/sources/oauth/dingtalk-directory/<source_slug>/departments/
GET  /api/v3/sources/oauth/dingtalk-directory/<source_slug>/users/
GET  /api/v3/sources/oauth/dingtalk-directory/<source_slug>/users/<corp_id>/<user_id>/org/
```

Permissions:

- `status` and list endpoints require `view_oauthsource` on the DingTalk source
  or global OAuth source view permission.
- `sync` requires `change_oauthsource` on the DingTalk source or global OAuth
  source change permission.
- The user org endpoint can return the current authenticated user's own org
  context when the requested `(corp_id, user_id)` matches their
  `User.attributes["dingtalk"]`.
- Reading other users requires `view_dingtalkdirectoryuser`.

Response constraints:

- Department list returns `corp_id`, `dept_id`, `name`, `parent_dept_id`,
  `is_deleted`, and `last_seen_at`.
- User list returns `corp_id`, `user_id`, `name`, `title`, `avatar`,
  `dept_id_list`, `manager_user_id`, `active`, `is_deleted`, and `last_seen_at`.
- User list does not return `mobile`, `email`, or `raw` unless a later
  permissioned serializer is added.

## Downstream Mapping Contract

OIDC scope mapping for current-user org context:

```python
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context

return {
    "dingtalk_org": get_dingtalk_org_context(
        request.user,
        source_slug="dingtalk",
        include_manager_chain=True,
        include_department_path=True,
    )
}
```

SAML property mapping for manager chain:

```python
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context

org = get_dingtalk_org_context(
    request.user,
    source_slug="dingtalk",
    include_manager_chain=True,
    include_department_path=False,
)
return [item["user_id"] for item in org.get("manager_chain", [])]
```

The helper must return a safe empty shape when:

- The user has no DingTalk attributes.
- The DingTalk source is missing or disabled.
- The directory cache has not synced.
- The user's manager data is absent.

Safe empty shape:

```python
{
    "corp_id": None,
    "user_id": None,
    "source_slug": "dingtalk",
    "departments": [],
    "manager": None,
    "manager_chain": [],
    "stale": True,
    "last_synced_at": None,
}
```

## Task 1: Lock Scope, Permissions, And Runtime Contract

**Files:**

- Read: `authentik/sources/oauth/types/dingtalk.py`
- Read: `authentik/sources/oauth/api/dingtalk_allowlist.py`
- Read: `docs/dingtalk-oauth-downstream-mappings.md`
- Create later: `docs/dingtalk-directory-org-service.md`

- [ ] **Step 1: Confirm existing DingTalk source contract**

Run:

```bash
nl -ba authentik/sources/oauth/types/dingtalk.py | sed -n '25,35p;216,263p;344,424p;540,571p'
nl -ba docs/dingtalk-oauth-downstream-mappings.md | sed -n '30,76p'
```

Expected:

- Existing login source uses `provider_type="dingtalk"`.
- Existing code already fetches app tokens and department child lists.
- Existing user attributes are stored under `User.attributes["dingtalk"]`.

- [ ] **Step 2: Record MVP boundary in operator docs**

Create `docs/dingtalk-directory-org-service.md` with this opening contract:

```markdown
# DingTalk Directory And Org Service

This feature makes authentik the controlled DingTalk read integration for
downstream applications.

Supported:

- Current-user organization context through OIDC/SAML mappings.
- Cached departments, users, direct manager, and manager chain.
- Admin-triggered and scheduled read-only directory sync.
- Optional read API for applications that are explicitly permitted.

Not supported:

- DingTalk writeback.
- Real-time DingTalk proxying during token issuance.
- Global release of full DingTalk raw profiles.
- Downstream access to DingTalk app secrets or access tokens.
```

- [ ] **Step 3: Commit contract**

Run:

```bash
git add docs/dingtalk-directory-org-service.md
git commit -m "docs: define dingtalk directory service contract"
```

Expected: one docs-only commit.

## Task 2: Add Directory Cache Models

**Files:**

- Modify: `authentik/sources/oauth/models.py`
- Create: `authentik/sources/oauth/migrations/00xx_dingtalk_directory.py`
- Test: `authentik/sources/oauth/tests/test_dingtalk_directory_sync.py`

- [ ] **Step 1: Write failing model tests**

Add:

```python
from django.test import TestCase
from django.utils.timezone import now

from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)


class TestDingTalkDirectoryModels(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def test_user_and_department_are_unique_per_source_and_corp(self):
        seen = now()
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="1",
            name="HQ",
            parent_dept_id="",
            last_seen_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            manager_user_id="MANAGER",
            dept_id_list=["1"],
            last_seen_at=seen,
        )
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
            counters={"users": 1, "departments": 1},
        )

        self.assertEqual(DingTalkDirectoryDepartment.objects.count(), 1)
        self.assertEqual(DingTalkDirectoryUser.objects.get().manager_user_id, "MANAGER")
        self.assertEqual(DingTalkDirectorySyncStatus.objects.get().counters["users"], 1)
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_dingtalk_directory_sync.py::TestDingTalkDirectoryModels -vv
```

Expected: FAIL because the models do not exist.

- [ ] **Step 3: Add models**

Add the three models from the **Data Model** section to
`authentik/sources/oauth/models.py`. Import `InternallyManagedMixin` if it is not
already imported in that file.

- [ ] **Step 4: Create migration**

Run:

```bash
uv run python -m manage makemigrations authentik_sources_oauth
```

Expected: a migration creating `DingTalkDirectorySyncStatus`,
`DingTalkDirectoryDepartment`, and `DingTalkDirectoryUser`.

- [ ] **Step 5: Run model test**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_dingtalk_directory_sync.py::TestDingTalkDirectoryModels -vv
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add authentik/sources/oauth/models.py authentik/sources/oauth/migrations/00xx_dingtalk_directory.py authentik/sources/oauth/tests/test_dingtalk_directory_sync.py
git commit -m "feat: add dingtalk directory cache models"
```

## Task 3: Extract A Reusable DingTalk Directory Client

**Files:**

- Create: `authentik/sources/oauth/dingtalk/__init__.py`
- Create: `authentik/sources/oauth/dingtalk/client.py`
- Modify: `authentik/sources/oauth/types/dingtalk.py`
- Test: `authentik/sources/oauth/tests/test_dingtalk_directory_client.py`

- [ ] **Step 1: Write failing client tests**

Add tests that verify request shape and normalization:

```python
from django.test import TestCase
from requests_mock import Mocker

from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
)


class TestDingTalkDirectoryClient(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def test_fetch_departments_normalizes_ids(self):
        with Mocker() as mocker:
            mocker.get(DINGTALK_APP_ACCESS_TOKEN_URL, json={"access_token": "APP_TOKEN"})
            mocker.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                [
                    {
                        "json": {
                            "errcode": 0,
                            "result": [
                                {"dept_id": 2, "name": "Engineering", "parent_id": 1},
                            ],
                        }
                    },
                    {"json": {"errcode": 0, "result": []}},
                ],
            )

            departments = list(DingTalkDirectoryClient(self.source).iter_departments())

        self.assertEqual(
            departments,
            [
                {
                    "dept_id": "2",
                    "name": "Engineering",
                    "parent_dept_id": "1",
                    "raw": {"dept_id": 2, "name": "Engineering", "parent_id": 1},
                }
            ],
        )
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_dingtalk_directory_client.py -vv
```

Expected: FAIL because `DingTalkDirectoryClient` does not exist.

- [ ] **Step 3: Implement client skeleton**

Create `authentik/sources/oauth/dingtalk/client.py`:

```python
from collections.abc import Iterator
from typing import Any

from requests import Session

from authentik.lib.utils.http import get_http_session
from authentik.sources.oauth.models import OAuthSource
from authentik.sources.oauth.types.dingtalk import (
    DINGTALK_APP_ACCESS_TOKEN_URL,
    DINGTALK_DEPARTMENT_LIST_URL,
    DINGTALK_USER_DETAIL_URL,
    _legacy_error,
)

DINGTALK_DEPARTMENT_USER_LIST_URL = "https://oapi.dingtalk.com/topapi/v2/user/list"
DINGTALK_MAX_DEPARTMENT_DEPTH = 50
DINGTALK_MAX_DEPARTMENTS = 10000
DINGTALK_PAGE_SIZE = 100


class DingTalkDirectoryClient:
    def __init__(self, source: OAuthSource, session: Session | None = None):
        self.source = source
        self.session = session or get_http_session()
        self._app_token = ""

    @property
    def app_token(self) -> str:
        if self._app_token:
            return self._app_token
        response = self.session.get(
            DINGTALK_APP_ACCESS_TOKEN_URL,
            params={"appkey": self.source.consumer_key, "appsecret": self.source.consumer_secret},
        )
        response.raise_for_status()
        data = response.json()
        error = _legacy_error(data)
        token = data.get("access_token") or data.get("accessToken")
        if error or not token:
            raise ValueError(error or "DingTalk app token response did not include a token.")
        self._app_token = token
        return token

    def iter_departments(self) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()

        def fetch_children(parent_id: str = "1", depth: int = 0) -> Iterator[dict[str, Any]]:
            if depth > DINGTALK_MAX_DEPARTMENT_DEPTH:
                raise ValueError("DingTalk department traversal depth limit exceeded.")
            response = self.session.post(
                DINGTALK_DEPARTMENT_LIST_URL,
                params={"access_token": self.app_token},
                json={"dept_id": parent_id},
            )
            response.raise_for_status()
            data = response.json()
            if error := _legacy_error(data):
                raise ValueError(error)
            result = data.get("result") or []
            if isinstance(result, dict):
                result = result.get("dept_id_list") or result.get("departments") or []
            if not isinstance(result, list | tuple | set):
                return
            for department in result:
                if not isinstance(department, dict):
                    continue
                dept_id = department.get("dept_id") or department.get("deptId")
                if dept_id is None:
                    continue
                dept_id = str(dept_id)
                if dept_id in seen:
                    continue
                if len(seen) >= DINGTALK_MAX_DEPARTMENTS:
                    raise ValueError("DingTalk department traversal department limit exceeded.")
                seen.add(dept_id)
                normalized = {
                    "dept_id": dept_id,
                    "name": department.get("name") or department.get("dept_name") or "",
                    "parent_dept_id": str(
                        department.get("parent_id") or department.get("parentId") or parent_id
                    ),
                    "raw": department,
                }
                yield normalized
                yield from fetch_children(dept_id, depth + 1)

        yield from fetch_children()

    def get_user_detail(self, user_id: str) -> dict[str, Any]:
        response = self.session.post(
            DINGTALK_USER_DETAIL_URL,
            params={"access_token": self.app_token},
            json={"userid": user_id},
        )
        response.raise_for_status()
        data = response.json()
        if error := _legacy_error(data):
            raise ValueError(error)
        return data.get("result") or {}
```

- [ ] **Step 4: Reuse client in existing department helper**

Change `fetch_dingtalk_departments()` in `authentik/sources/oauth/types/dingtalk.py`
to call `DingTalkDirectoryClient(source).iter_departments()` and keep the same
return shape:

```python
def fetch_dingtalk_departments(source: OAuthSource, corp_id: str) -> dict[str, Any]:
    from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient

    departments = []
    for department in DingTalkDirectoryClient(source).iter_departments():
        departments.append(
            {
                "dept_id": department["dept_id"],
                "name": department["name"],
                "parent_id": department["parent_dept_id"],
            }
        )
    return {"corp_id": corp_id, "departments": departments}
```

- [ ] **Step 5: Run client and allowlist regression tests**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_dingtalk_directory_client.py authentik/sources/oauth/tests/test_api_dingtalk_allowlist.py -vv
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add authentik/sources/oauth/dingtalk authentik/sources/oauth/types/dingtalk.py authentik/sources/oauth/tests/test_dingtalk_directory_client.py
git commit -m "feat: add reusable dingtalk directory client"
```

## Task 4: Implement Directory Sync

**Files:**

- Create: `authentik/sources/oauth/dingtalk/sync.py`
- Modify: `authentik/sources/oauth/tasks.py`
- Modify: `authentik/sources/oauth/apps.py`
- Test: `authentik/sources/oauth/tests/test_dingtalk_directory_sync.py`

- [ ] **Step 1: Add failing sync tests**

Add:

```python
from unittest.mock import patch

from django.test import TestCase
from django.utils.timezone import now

from authentik.sources.oauth.dingtalk.sync import sync_dingtalk_directory
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)


class TestDingTalkDirectorySync(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    @patch("authentik.sources.oauth.dingtalk.sync.DingTalkDirectoryClient")
    def test_sync_upserts_departments_and_users(self, client_cls):
        client = client_cls.return_value
        client.iter_departments.return_value = [
            {"dept_id": "1", "name": "HQ", "parent_dept_id": "", "raw": {"dept_id": 1}},
        ]
        client.iter_department_users.return_value = [
            {
                "userid": "USER",
                "unionid": "UNION",
                "name": "Ada",
                "title": "Engineer",
                "manager_userid": "MANAGER",
                "dept_id_list": [1],
                "active": True,
            }
        ]

        result = sync_dingtalk_directory(self.source, corp_id="CORP")

        self.assertEqual(result["departments"], 1)
        self.assertEqual(result["users"], 1)
        self.assertEqual(DingTalkDirectoryDepartment.objects.get().dept_id, "1")
        user = DingTalkDirectoryUser.objects.get()
        self.assertEqual(user.user_id, "USER")
        self.assertEqual(user.manager_user_id, "MANAGER")
        self.assertEqual(user.dept_id_list, ["1"])
        self.assertEqual(DingTalkDirectorySyncStatus.objects.get().status, "success")
```

- [ ] **Step 2: Add `iter_department_users()` to client**

Implement pagination in `DingTalkDirectoryClient`:

```python
def iter_department_users(self, dept_id: str) -> Iterator[dict[str, Any]]:
    cursor = 0
    while True:
        response = self.session.post(
            DINGTALK_DEPARTMENT_USER_LIST_URL,
            params={"access_token": self.app_token},
            json={"dept_id": dept_id, "cursor": cursor, "size": DINGTALK_PAGE_SIZE},
        )
        response.raise_for_status()
        data = response.json()
        if error := _legacy_error(data):
            raise ValueError(error)
        result = data.get("result") or {}
        users = result.get("list") or []
        for user in users:
            if isinstance(user, dict):
                yield user
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor") or result.get("nextCursor") or 0
```

- [ ] **Step 3: Implement normalization**

Create `authentik/sources/oauth/dingtalk/sync.py`:

```python
from typing import Any

from django.db import transaction
from django.utils.timezone import now

from authentik.sources.oauth.dingtalk.client import DingTalkDirectoryClient
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)


def normalize_id_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple | set):
        return []
    return sorted({str(item) for item in value if item is not None})


def normalize_dingtalk_user(raw: dict[str, Any], corp_id: str) -> dict[str, Any]:
    user_id = raw.get("userid") or raw.get("userId") or raw.get("user_id")
    if not user_id:
        raise ValueError("DingTalk user did not include userid.")
    return {
        "corp_id": str(corp_id),
        "user_id": str(user_id),
        "union_id": raw.get("unionid") or raw.get("unionId") or "",
        "open_id": raw.get("openId") or raw.get("open_id") or "",
        "name": raw.get("name") or raw.get("nick") or "",
        "avatar": raw.get("avatar") or raw.get("avatarUrl") or "",
        "title": raw.get("title") or "",
        "email": raw.get("email") or "",
        "mobile": raw.get("mobile") or "",
        "job_number": raw.get("job_number") or raw.get("jobNumber") or "",
        "manager_user_id": raw.get("manager_userid") or raw.get("managerUserId") or "",
        "dept_id_list": normalize_id_list(raw.get("dept_id_list") or raw.get("deptIdList")),
        "active": bool(raw.get("active", True)),
        "raw": raw,
    }
```

- [ ] **Step 4: Implement sync orchestration**

Add:

```python
@transaction.atomic
def sync_dingtalk_directory(source: OAuthSource, corp_id: str) -> dict[str, int]:
    if source.provider_type != "dingtalk":
        raise ValueError("Source is not a DingTalk OAuth source.")
    started_at = now()
    status, _ = DingTalkDirectorySyncStatus.objects.update_or_create(
        source=source,
        corp_id=str(corp_id),
        defaults={"status": "running", "started_at": started_at, "error": ""},
    )
    client = DingTalkDirectoryClient(source)
    counters = {"departments": 0, "users": 0}
    seen_depts: set[str] = set()
    seen_users: set[str] = set()
    try:
        departments = list(client.iter_departments())
        for department in departments:
            seen_depts.add(department["dept_id"])
            DingTalkDirectoryDepartment.objects.update_or_create(
                source=source,
                corp_id=str(corp_id),
                dept_id=department["dept_id"],
                defaults={
                    "name": department["name"],
                    "parent_dept_id": department["parent_dept_id"],
                    "raw": department["raw"],
                    "is_deleted": False,
                    "last_seen_at": started_at,
                },
            )
            counters["departments"] += 1
            for raw_user in client.iter_department_users(department["dept_id"]):
                user = normalize_dingtalk_user(raw_user, str(corp_id))
                seen_users.add(user["user_id"])
                DingTalkDirectoryUser.objects.update_or_create(
                    source=source,
                    corp_id=str(corp_id),
                    user_id=user["user_id"],
                    defaults={
                        **user,
                        "is_deleted": False,
                        "last_seen_at": started_at,
                    },
                )
                counters["users"] += 1

        DingTalkDirectoryDepartment.objects.filter(source=source, corp_id=str(corp_id)).exclude(
            dept_id__in=seen_depts
        ).update(is_deleted=True)
        DingTalkDirectoryUser.objects.filter(source=source, corp_id=str(corp_id)).exclude(
            user_id__in=seen_users
        ).update(is_deleted=True)
        status.status = "success"
        status.error = ""
        status.counters = counters
        status.finished_at = now()
        status.save()
        return counters
    except Exception as exc:
        status.status = "error"
        status.error = str(exc)
        status.counters = counters
        status.finished_at = now()
        status.save()
        raise
```

- [ ] **Step 5: Add Dramatiq actors**

In `authentik/sources/oauth/tasks.py`:

```python
@actor(description=_("Sync DingTalk directory cache."))
def dingtalk_directory_sync(source_pk: str, corp_id: str):
    source = OAuthSource.objects.filter(pk=source_pk, enabled=True, provider_type="dingtalk").first()
    if not source:
        return
    from authentik.sources.oauth.dingtalk.sync import sync_dingtalk_directory

    return sync_dingtalk_directory(source, corp_id)


@actor(description=_("Sync all DingTalk directory caches."))
def dingtalk_directory_sync_all():
    from authentik.sources.oauth.models import UserOAuthSourceConnection

    for source in OAuthSource.objects.filter(enabled=True, provider_type="dingtalk"):
        corp_ids = (
            UserOAuthSourceConnection.objects.filter(
                source=source,
                user__attributes__has_key="dingtalk",
            )
            .values_list("user__attributes__dingtalk__corp_id", flat=True)
            .distinct()
        )
        for corp_id in corp_ids:
            if corp_id:
                dingtalk_directory_sync.send(str(source.pk), str(corp_id))
```

- [ ] **Step 6: Add tenant schedule**

In `authentik/sources/oauth/apps.py`, extend the existing schedule list:

```python
from authentik.sources.oauth.tasks import dingtalk_directory_sync_all, update_well_known_jwks

return [
    ScheduleSpec(
        actor=update_well_known_jwks,
        crontab=f"{fqdn_rand('update_well_known_jwks')} */3 * * *",
    ),
    ScheduleSpec(
        actor=dingtalk_directory_sync_all,
        crontab=f"{fqdn_rand('dingtalk_directory_sync_all')} */2 * * *",
    ),
]
```

- [ ] **Step 7: Run sync tests**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_dingtalk_directory_sync.py -vv
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add authentik/sources/oauth/dingtalk/client.py authentik/sources/oauth/dingtalk/sync.py authentik/sources/oauth/tasks.py authentik/sources/oauth/apps.py authentik/sources/oauth/tests/test_dingtalk_directory_sync.py
git commit -m "feat: sync dingtalk directory cache"
```

## Task 5: Add Org Context Selectors

**Files:**

- Create: `authentik/sources/oauth/dingtalk/selectors.py`
- Test: `authentik/sources/oauth/tests/test_dingtalk_directory_sync.py`
- Modify: `docs/dingtalk-oauth-downstream-mappings.md`

- [ ] **Step 1: Write failing selector tests**

Add:

```python
from django.utils.timezone import now

from authentik.core.tests.utils import create_test_user
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context


class TestDingTalkOrgContext(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )

    def test_org_context_returns_department_path_and_manager_chain(self):
        seen = now()
        DingTalkDirectorySyncStatus.objects.create(
            source=self.source,
            corp_id="CORP",
            status="success",
            finished_at=seen,
        )
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="1",
            name="HQ",
            parent_dept_id="",
            last_seen_at=seen,
        )
        DingTalkDirectoryDepartment.objects.create(
            source=self.source,
            corp_id="CORP",
            dept_id="2",
            name="Engineering",
            parent_dept_id="1",
            last_seen_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="MANAGER",
            name="Grace",
            title="Director",
            dept_id_list=["1"],
            last_seen_at=seen,
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            title="Engineer",
            manager_user_id="MANAGER",
            dept_id_list=["2"],
            last_seen_at=seen,
        )
        user = create_test_user("ada")
        user.attributes = {"dingtalk": {"corp_id": "CORP", "user_id": "USER"}}
        user.save()

        context = get_dingtalk_org_context(user, source_slug="dingtalk")

        self.assertEqual(context["departments"][0]["path"][0]["name"], "HQ")
        self.assertEqual(context["departments"][0]["path"][1]["name"], "Engineering")
        self.assertEqual(context["manager"]["user_id"], "MANAGER")
        self.assertEqual(context["manager_chain"][0]["name"], "Grace")
        self.assertFalse(context["stale"])
```

- [ ] **Step 2: Run failing selector tests**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_dingtalk_directory_sync.py::TestDingTalkOrgContext -vv
```

Expected: FAIL because `selectors.py` does not exist.

- [ ] **Step 3: Implement selector helpers**

Create `authentik/sources/oauth/dingtalk/selectors.py`:

```python
from datetime import timedelta
from typing import Any

from django.utils.timezone import now

from authentik.core.models import User
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)

MAX_MANAGER_CHAIN_DEPTH = 20
STALE_AFTER = timedelta(hours=24)


def empty_org_context(source_slug: str) -> dict[str, Any]:
    return {
        "corp_id": None,
        "user_id": None,
        "source_slug": source_slug,
        "departments": [],
        "manager": None,
        "manager_chain": [],
        "stale": True,
        "last_synced_at": None,
    }


def _public_user(user: DingTalkDirectoryUser) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "name": user.name,
        "title": user.title,
        "avatar": user.avatar,
    }


def _department_path(source: OAuthSource, corp_id: str, dept_id: str) -> list[dict[str, str]]:
    departments = {
        item.dept_id: item
        for item in DingTalkDirectoryDepartment.objects.filter(
            source=source,
            corp_id=corp_id,
            is_deleted=False,
        )
    }
    path = []
    seen = set()
    current = departments.get(dept_id)
    while current and current.dept_id not in seen:
        seen.add(current.dept_id)
        path.append({"dept_id": current.dept_id, "name": current.name})
        current = departments.get(current.parent_dept_id)
    return list(reversed(path))


def _manager_chain(source: OAuthSource, corp_id: str, start: DingTalkDirectoryUser) -> list[dict[str, Any]]:
    chain = []
    seen = {start.user_id}
    manager_user_id = start.manager_user_id
    for _ in range(MAX_MANAGER_CHAIN_DEPTH):
        if not manager_user_id or manager_user_id in seen:
            break
        manager = DingTalkDirectoryUser.objects.filter(
            source=source,
            corp_id=corp_id,
            user_id=manager_user_id,
            is_deleted=False,
        ).first()
        if not manager:
            break
        seen.add(manager.user_id)
        chain.append(_public_user(manager))
        manager_user_id = manager.manager_user_id
    return chain


def get_dingtalk_org_context(
    user: User,
    source_slug: str = "dingtalk",
    include_manager_chain: bool = True,
    include_department_path: bool = True,
) -> dict[str, Any]:
    context = empty_org_context(source_slug)
    dingtalk = (user.attributes or {}).get("dingtalk") or {}
    corp_id = dingtalk.get("corp_id") or dingtalk.get("corpId")
    user_id = dingtalk.get("user_id") or dingtalk.get("userid") or dingtalk.get("userId")
    if not corp_id or not user_id:
        return context
    source = OAuthSource.objects.filter(slug=source_slug, provider_type="dingtalk", enabled=True).first()
    if not source:
        return context
    status = DingTalkDirectorySyncStatus.objects.filter(source=source, corp_id=str(corp_id)).first()
    last_synced_at = status.finished_at if status else None
    stale = not last_synced_at or last_synced_at < now() - STALE_AFTER
    directory_user = DingTalkDirectoryUser.objects.filter(
        source=source,
        corp_id=str(corp_id),
        user_id=str(user_id),
        is_deleted=False,
    ).first()
    context.update(
        {
            "corp_id": str(corp_id),
            "user_id": str(user_id),
            "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
            "stale": stale,
        }
    )
    if not directory_user:
        return context
    departments = []
    for dept_id in directory_user.dept_id_list:
        department = DingTalkDirectoryDepartment.objects.filter(
            source=source,
            corp_id=str(corp_id),
            dept_id=str(dept_id),
            is_deleted=False,
        ).first()
        if not department:
            continue
        value = {
            "dept_id": department.dept_id,
            "name": department.name,
            "parent_id": department.parent_dept_id,
        }
        if include_department_path:
            value["path"] = _department_path(source, str(corp_id), department.dept_id)
        departments.append(value)
    chain = _manager_chain(source, str(corp_id), directory_user) if include_manager_chain else []
    context["departments"] = departments
    context["manager_chain"] = chain
    context["manager"] = chain[0] if chain else None
    return context
```

- [ ] **Step 4: Run selector tests**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_dingtalk_directory_sync.py::TestDingTalkOrgContext -vv
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add authentik/sources/oauth/dingtalk/selectors.py authentik/sources/oauth/tests/test_dingtalk_directory_sync.py
git commit -m "feat: compute dingtalk org context"
```

## Task 6: Add Directory API

**Files:**

- Create: `authentik/sources/oauth/api/dingtalk_directory.py`
- Modify: `authentik/sources/oauth/urls.py`
- Test: `authentik/sources/oauth/tests/test_api_dingtalk_directory.py`

- [ ] **Step 1: Write permission and response tests**

Add tests:

```python
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from authentik.core.tests.utils import create_test_admin_user, create_test_user
from authentik.sources.oauth.models import DingTalkDirectoryUser, OAuthSource


class TestDingTalkDirectoryAPI(TestCase):
    def setUp(self):
        self.source = OAuthSource.objects.create(
            name="DingTalk",
            slug="dingtalk",
            provider_type="dingtalk",
            consumer_key="CLIENT_ID",
            consumer_secret="CLIENT_SECRET",
        )
        DingTalkDirectoryUser.objects.create(
            source=self.source,
            corp_id="CORP",
            user_id="USER",
            name="Ada",
            mobile="13800000000",
            email="ada@example.invalid",
            dept_id_list=["1"],
            last_seen_at=now(),
        )

    def test_user_list_requires_admin_permission(self):
        self.client.force_login(create_test_user("regular"))
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )
        self.assertEqual(response.status_code, 403)

    def test_user_list_hides_sensitive_fields(self):
        self.client.force_login(create_test_admin_user())
        response = self.client.get(
            reverse("authentik_api:dingtalk-directory-users", kwargs={"source_slug": "dingtalk"})
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertEqual(item["user_id"], "USER")
        self.assertNotIn("mobile", item)
        self.assertNotIn("email", item)
        self.assertNotIn("raw", item)
```

- [ ] **Step 2: Implement serializers and permissions**

In `authentik/sources/oauth/api/dingtalk_directory.py`:

```python
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context
from authentik.sources.oauth.models import (
    DingTalkDirectoryDepartment,
    DingTalkDirectorySyncStatus,
    DingTalkDirectoryUser,
    OAuthSource,
)
from authentik.sources.oauth.tasks import dingtalk_directory_sync


def get_dingtalk_source(source_slug: str) -> OAuthSource:
    return OAuthSource.objects.get(slug=source_slug, provider_type="dingtalk")


class CanViewDingTalkDirectory(BasePermission):
    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        source = get_dingtalk_source(view.kwargs["source_slug"])
        view.dingtalk_source = source
        return bool(
            request.user.has_perm("authentik_sources_oauth.view_oauthsource")
            or request.user.has_perm("authentik_sources_oauth.view_oauthsource", source)
        )


class CanChangeDingTalkDirectory(CanViewDingTalkDirectory):
    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        source = view.dingtalk_source
        return bool(
            request.user.has_perm("authentik_sources_oauth.change_oauthsource")
            or request.user.has_perm("authentik_sources_oauth.change_oauthsource", source)
        )


class DingTalkDirectoryUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = DingTalkDirectoryUser
        fields = [
            "corp_id",
            "user_id",
            "name",
            "title",
            "avatar",
            "dept_id_list",
            "manager_user_id",
            "active",
            "is_deleted",
            "last_seen_at",
        ]
```

- [ ] **Step 3: Implement status, sync, and list views**

Add:

```python
class DingTalkDirectoryStatusView(APIView):
    permission_classes = [CanViewDingTalkDirectory]

    def get(self, request, source_slug: str) -> Response:
        source = self.dingtalk_source
        statuses = DingTalkDirectorySyncStatus.objects.filter(source=source).order_by("corp_id")
        return Response(
            {
                "source_slug": source.slug,
                "sync": [
                    {
                        "corp_id": item.corp_id,
                        "status": item.status,
                        "started_at": item.started_at,
                        "finished_at": item.finished_at,
                        "error": item.error,
                        "counters": item.counters,
                    }
                    for item in statuses
                ],
            }
        )


class DingTalkDirectorySyncView(APIView):
    permission_classes = [CanChangeDingTalkDirectory]

    def post(self, request, source_slug: str) -> Response:
        source = self.dingtalk_source
        corp_id = request.data.get("corp_id") or request.data.get("corpId")
        if not corp_id:
            raise ValidationError({"corp_id": "This field is required."})
        dingtalk_directory_sync.send(str(source.pk), str(corp_id))
        return Response({"queued": True, "corp_id": str(corp_id)})
```

Implement list views with pagination using DRF generics or existing authentik
viewset patterns. Keep serializers narrow and add tests for every released field.

- [ ] **Step 4: Register routes**

In `authentik/sources/oauth/urls.py`, add URL patterns:

```python
path(
    "sources/oauth/dingtalk-directory/<slug:source_slug>/status/",
    DingTalkDirectoryStatusView.as_view(),
    name="dingtalk-directory-status",
),
path(
    "sources/oauth/dingtalk-directory/<slug:source_slug>/sync/",
    DingTalkDirectorySyncView.as_view(),
    name="dingtalk-directory-sync",
),
```

Add the remaining routes with stable names used by the tests:

```python
path(
    "sources/oauth/dingtalk-directory/<slug:source_slug>/departments/",
    DingTalkDirectoryDepartmentsView.as_view(),
    name="dingtalk-directory-departments",
),
path(
    "sources/oauth/dingtalk-directory/<slug:source_slug>/users/",
    DingTalkDirectoryUsersView.as_view(),
    name="dingtalk-directory-users",
),
path(
    "sources/oauth/dingtalk-directory/<slug:source_slug>/users/<str:corp_id>/<str:user_id>/org/",
    DingTalkDirectoryUserOrgView.as_view(),
    name="dingtalk-directory-user-org",
),
```

- [ ] **Step 5: Run API tests**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_api_dingtalk_directory.py -vv
```

Expected: PASS.

- [ ] **Step 6: Regenerate schema and clients**

Run:

```bash
make gen-build
make gen-client-ts
```

Expected: `schema.yml`, `blueprints/schema.json`, and generated TypeScript client
include the DingTalk directory endpoints.

- [ ] **Step 7: Commit**

Run:

```bash
git add authentik/sources/oauth/api/dingtalk_directory.py authentik/sources/oauth/urls.py authentik/sources/oauth/tests/test_api_dingtalk_directory.py schema.yml blueprints/schema.json packages/client-ts/src
git commit -m "feat: expose dingtalk directory api"
```

## Task 7: Add Downstream Mapping Docs

**Files:**

- Modify: `docs/dingtalk-oauth-downstream-mappings.md`
- Modify: `docs/dingtalk-directory-org-service.md`

- [ ] **Step 1: Add current-user org context section**

Append to `docs/dingtalk-oauth-downstream-mappings.md`:

~~~~markdown
## DingTalk Organization Context Claims

Applications that need the current user's department names, department path,
direct manager, or manager chain should use the cached organization helper
instead of calling DingTalk directly.

OIDC example:

```python
from authentik.sources.oauth.dingtalk.selectors import get_dingtalk_org_context

return {
    "dingtalk_org": get_dingtalk_org_context(
        request.user,
        source_slug="dingtalk",
        include_manager_chain=True,
        include_department_path=True,
    )
}
```

Only assign this mapping to providers whose application owner is approved to
receive organization relationship data.
~~~~

- [ ] **Step 2: Add direct integration decision rule**

Append:

```markdown
## When A Downstream Application Should Still Connect To DingTalk

Use direct DingTalk integration only when the application needs:

- DingTalk write APIs.
- Data fresher than the configured authentik sync interval.
- DingTalk APIs outside users, departments, and manager relationships.
- Tenant-specific DingTalk permissions that should not be shared with authentik.

For current-user identity and organization context, prefer authentik claims.
For read-only directory lookup, prefer the authenticated DingTalk directory API.
```

- [ ] **Step 3: Run placeholder scan**

Run:

```bash
pattern="TB[D]|TO[D]O|implement late[r]|fill i[n]|appropriate error handlin[g]|similar t[o]"
rg -n "$pattern" docs/dingtalk-oauth-downstream-mappings.md docs/dingtalk-directory-org-service.md
```

Expected: no output.

- [ ] **Step 4: Commit**

Run:

```bash
git add docs/dingtalk-oauth-downstream-mappings.md docs/dingtalk-directory-org-service.md
git commit -m "docs: document dingtalk org context mappings"
```

## Task 8: Add Admin Sync Status Panel

**Files:**

- Create: `web/src/admin/sources/oauth/DingTalkDirectoryPanel.ts`
- Modify: `web/src/admin/sources/oauth/OAuthSourceViewPage.ts`
- Test: `web/test/unit/dingtalk-directory-panel.test.ts`

- [ ] **Step 1: Write frontend state tests**

Add tests for status rendering and manual sync call:

```typescript
import { expect } from "@open-wc/testing";
import { dingtalkDirectoryStatusSummary } from "#admin/sources/oauth/DingTalkDirectoryPanel";

describe("dingtalkDirectoryStatusSummary", () => {
    it("counts successful and failed corp syncs", () => {
        const result = dingtalkDirectoryStatusSummary([
            { corp_id: "CORP_A", status: "success" },
            { corp_id: "CORP_B", status: "error" },
        ]);
        expect(result.success).to.equal(1);
        expect(result.error).to.equal(1);
    });
});
```

- [ ] **Step 2: Implement panel**

Create a PatternFly card inside the OAuth source detail page. Required controls:

- Status refresh button.
- Corp ID input.
- Manual sync button.
- Table of per-corp sync status, last successful sync, counters, and error.
- Links to the directory docs.

Panel rules:

- Render only for `ProviderTypeEnum.Dingtalk`.
- Use existing `ak-spinner-button`, `ak-empty-state`, and PatternFly table/card
  classes.
- Do not add a standalone route or sidebar item.

- [ ] **Step 3: Wire panel into `OAuthSourceViewPage.ts`**

Add import:

```typescript
import "./DingTalkDirectoryPanel";
```

Insert the directory panel immediately after the existing DingTalk allowlist
tabpanel and before `<ak-rbac-object-permission-page>`, using the existing
`this.source` property:

```typescript
${this.source.providerType === ProviderTypeEnum.Dingtalk
    ? html`<div
          role="tabpanel"
          tabindex="0"
          slot="page-dingtalk-directory"
          id="page-dingtalk-directory"
          aria-label="${msg("DingTalk Directory", {
              id: "sources.oauth.dingtalk-directory.title",
          })}"
          class="pf-c-page__main-section pf-m-no-padding-mobile"
      >
          <ak-source-oauth-dingtalk-directory
              .source=${this.source}
          ></ak-source-oauth-dingtalk-directory>
      </div>`
    : nothing}
```

- [ ] **Step 4: Run frontend tests**

Run:

```bash
make web-test
make web-check-compile
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add web/src/admin/sources/oauth/DingTalkDirectoryPanel.ts web/src/admin/sources/oauth/OAuthSourceViewPage.ts web/test/unit/dingtalk-directory-panel.test.ts
git commit -m "web: add dingtalk directory status panel"
```

## Task 9: Verification And Rollout

**Files:**

- Modify: `docs/dingtalk-directory-org-service.md`
- Read: `docs/dingtalk-allowlist-runbook.md`

- [ ] **Step 1: Run backend test suite for touched areas**

Run:

```bash
uv run pytest authentik/sources/oauth/tests/test_type_dingtalk.py authentik/sources/oauth/tests/test_api_dingtalk_allowlist.py authentik/sources/oauth/tests/test_dingtalk_directory_client.py authentik/sources/oauth/tests/test_dingtalk_directory_sync.py authentik/sources/oauth/tests/test_api_dingtalk_directory.py -vv
```

Expected: PASS.

- [ ] **Step 2: Run generated API and web checks**

Run:

```bash
make gen-build
make gen-client-ts
make web-check-compile
```

Expected: PASS with no unreviewed generated files outside expected schema/client
artifacts.

- [ ] **Step 3: Manual DingTalk tenant validation**

Record these checks in the private deployment record:

```text
1. DingTalk source has app permissions for department list, department user list, and user detail.
2. Manual sync for one corp_id queues and completes.
3. Department names and parent IDs match DingTalk admin output.
4. User manager_user_id matches DingTalk user detail output for at least two users.
5. Manager chain stops at top-level manager and handles missing manager safely.
6. OIDC test app receives dingtalk_org only when its provider includes the mapping.
7. SAML test app receives manager chain only when its provider includes the mapping.
8. An app without mappings receives no DingTalk org context.
9. Directory user list hides mobile, email, and raw profile in default serializer.
10. Stale flag becomes true when latest sync is older than the configured threshold.
```

- [ ] **Step 4: Update runbook**

Add a rollout section to `docs/dingtalk-directory-org-service.md`:

```markdown
## Rollout Checklist

1. Confirm DingTalk app OpenAPI permissions.
2. Deploy migrations.
3. Trigger manual sync for one real corp ID.
4. Verify sync status and counters.
5. Add OIDC/SAML mappings only to approved applications.
6. Confirm an unapproved application receives no DingTalk organization claims.
7. Enable scheduled sync.
8. Monitor sync errors for 24 hours.
```

- [ ] **Step 5: Commit rollout docs**

Run:

```bash
git add docs/dingtalk-directory-org-service.md
git commit -m "docs: add dingtalk directory rollout checklist"
```

## Task 10: Final Risk Review

**Files:**

- Review all changed files from Tasks 1-9.

- [ ] **Step 1: Check for source-specific scope creep**

Run:

```bash
git diff --name-only main...HEAD
```

Expected:

- DingTalk-specific files under `authentik/sources/oauth/dingtalk/`.
- DingTalk directory API under `authentik/sources/oauth/api/`.
- OAuth source models/migrations.
- OAuth tasks/apps/urls.
- DingTalk docs.
- Optional DingTalk source detail UI.
- Generated schema/client files.

No changes should appear under `authentik/core/models.py`,
`authentik/core/expression/evaluator.py`, or generic OAuth callback/redirect
files unless the implementation already has a focused failing test justifying
the change.

- [ ] **Step 2: Check privacy defaults**

Run:

```bash
rg -n "mobile|email|raw" authentik/sources/oauth/api/dingtalk_directory.py docs/dingtalk-oauth-downstream-mappings.md docs/dingtalk-directory-org-service.md
```

Expected:

- API serializer excludes `mobile`, `email`, and `raw`.
- Docs mention those fields only as restricted data.

- [ ] **Step 3: Check token path does not call DingTalk**

Run:

```bash
rg -n "DingTalkDirectoryClient|get_http_session|requests" authentik/providers authentik/sources/oauth/dingtalk/selectors.py
```

Expected:

- `selectors.py` reads local models only.
- OIDC/SAML provider paths do not call DingTalk live.

- [ ] **Step 4: Run final focused verification**

Run:

```bash
uv run pytest authentik/sources/oauth/tests -k "dingtalk" -vv
make web-check-compile
```

Expected: PASS.

- [ ] **Step 5: Commit final cleanup**

Run:

```bash
git status --short
git add .
git commit -m "test: verify dingtalk directory service"
```

Only run this commit if verification produced intentional doc, test, or generated
changes. If `git status --short` is clean, skip the commit.

## Execution Order

Use this order:

1. Task 1: contract and docs boundary.
2. Task 2: models and migration.
3. Task 3: reusable client.
4. Task 4: sync task and schedule.
5. Task 5: selectors and org context.
6. Task 6: API.
7. Task 7: downstream mapping docs.
8. Task 8: admin UI panel.
9. Task 9: verification and rollout.
10. Task 10: final risk review.

Parallelizable work after Task 2:

- Task 3 client and Task 5 selector tests can be drafted in parallel, but merge
  Task 3 first because selectors depend on normalized model fields.
- Task 7 docs can run in parallel with Task 6 API once the selector function
  signature is fixed.
- Task 8 frontend can run after Task 6 response shapes are fixed.

## Acceptance Criteria

- A DingTalk source can sync departments and users for a configured `corp_id`.
- Cached users store `manager_user_id` when DingTalk returns it.
- Current-user org context returns department path and manager chain from local
  cache without calling DingTalk.
- OIDC/SAML mappings can expose org context per application.
- Applications without those mappings receive no new DingTalk organization data.
- Directory API hides sensitive fields by default and enforces authentik
  permissions.
- Sync failures do not erase the last successful cache.
- Stale data is visible through `stale=True`.
- Existing DingTalk login and allowlist tests still pass.

## Self-Review

- Spec coverage: login-time current-user attributes, directory sync, department
  names, manager tree, downstream claims, optional API, admin status, privacy,
  and rollout are covered.
- Placeholder scan: no deferred placeholders are used in task steps.
- Type consistency: all tasks use `corp_id`, `user_id`, `manager_user_id`,
  `DingTalkDirectoryUser`, `DingTalkDirectoryDepartment`, and
  `DingTalkDirectorySyncStatus` consistently.
- Scope control: generic authentik core and generic OAuth callback code remain
  outside the MVP.

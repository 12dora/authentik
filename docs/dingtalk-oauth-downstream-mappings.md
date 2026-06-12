# DingTalk OAuth Downstream Mappings

DingTalk OAuth source data is stored on the authentik user object under
`User.attributes["dingtalk"]`. The DingTalk source adapter must not hard-code
claims for downstream applications. Instead, expose only the DingTalk values a
specific downstream application needs by assigning per-application OIDC
`ScopeMapping` objects or SAML `SAMLPropertyMapping` objects.

For the full project customization overview, including DingTalk login,
localization, icon, OpenAPI permissions, and deployment notes, see
`docs/jiefa-authentik-customizations.md`.

## Implementation Phases

The downstream rollout can run in parallel with most of the DingTalk OAuth
adapter work once the `User.attributes["dingtalk"]` contract is fixed.

| Phase | Parallel Work | Completion Gate |
| --- | --- | --- |
| 1. Claim contract | Confirm the normalized keys for `name`, `avatar`, `title`, `user_id`, `union_id`, `corp_id`, and department/role data. | The adapter stores these values under `User.attributes["dingtalk"]`. |
| 2. OIDC mappings | Create OIDC `ScopeMapping` objects for applications that need DingTalk claims. This can run in parallel with SAML mapping design. | Each OIDC provider includes only the mappings required by its application. |
| 3. SAML mappings | Create SAML `SAMLPropertyMapping` objects for applications that need DingTalk claims. This can run in parallel with OIDC mapping design. | Each SAML provider includes only the attributes required by its application. |
| 4. App verification | Test OIDC userinfo/id_token claims and SAML assertions per application. | Name, title, avatar, and selected IDs appear only in the intended downstream app. |
| 5. Privacy review | Review mapped fields with the app owner or data owner. | No provider maps `dingtalk.raw_profile`; sensitive fields are released only by explicit need. |

For large rollouts, split Phase 2 and Phase 3 by downstream application. One
agent or operator can configure the HR app while another configures an internal
portal, because each authentik provider owns its own mappings.

## Normalized DingTalk Fields

The adapter should make these stable fields available for mappings:

```text
dingtalk.name
dingtalk.nick
dingtalk.avatar
dingtalk.title
dingtalk.user_id
dingtalk.union_id
dingtalk.open_id
dingtalk.corp_id
dingtalk.mobile
dingtalk.state_code
dingtalk.dept_id_list
dingtalk.job_number
dingtalk.role_list
```

Prefer `dingtalk.name` for real name, fall back to `dingtalk.nick` only when
the enhanced directory profile is unavailable. Prefer `dingtalk.avatar` for the
profile picture claim and `dingtalk.title` for job title or position.

## OIDC ScopeMapping Claims

Create an OIDC `ScopeMapping` for the provider used by the downstream
application, then return only the claims that application is allowed to receive.

Example expression:

```python
dingtalk = request.user.attributes.get("dingtalk", {})
return {
    "name": dingtalk.get("name") or dingtalk.get("nick"),
    "picture": dingtalk.get("avatar"),
    "dingtalk_title": dingtalk.get("title"),
    "dingtalk_user_id": dingtalk.get("user_id"),
    "dingtalk_union_id": dingtalk.get("union_id"),
    "dingtalk_dept_ids": dingtalk.get("dept_id_list", []),
}
```

Assign this mapping only to the OIDC provider for the application that needs
these claims. Applications without this mapping will not receive these DingTalk
claims through their userinfo response or ID token.

When a downstream OIDC application expects standard profile claims, map DingTalk
data into standard claim names:

```python
dingtalk = request.user.attributes.get("dingtalk", {})
return {
    "name": dingtalk.get("name") or dingtalk.get("nick"),
    "picture": dingtalk.get("avatar"),
    "profile": dingtalk.get("avatar"),
    "dingtalk_title": dingtalk.get("title"),
}
```

Keep the job title as a custom claim such as `dingtalk_title` unless the
downstream application has a documented standard claim for position.

## SAML Property Mappings

Create SAML `SAMLPropertyMapping` objects for the SAML provider used by the
downstream application. Use attribute names that are meaningful to that
application and return the corresponding value from
`request.user.attributes["dingtalk"]`.

Recommended SAML attribute names:

```text
dingtalkName
dingtalkUserId
dingtalkUnionId
dingtalkTitle
dingtalkAvatar
dingtalkDepartmentIds
```

Example expressions:

```python
# dingtalkName
dingtalk = request.user.attributes.get("dingtalk", {})
return dingtalk.get("name") or dingtalk.get("nick")
```

```python
# dingtalkUserId
dingtalk = request.user.attributes.get("dingtalk", {})
return dingtalk.get("user_id")
```

```python
# dingtalkUnionId
dingtalk = request.user.attributes.get("dingtalk", {})
return dingtalk.get("union_id")
```

```python
# dingtalkTitle
dingtalk = request.user.attributes.get("dingtalk", {})
return dingtalk.get("title")
```

```python
# dingtalkAvatar
dingtalk = request.user.attributes.get("dingtalk", {})
return dingtalk.get("avatar")
```

```python
# dingtalkDepartmentIds
dingtalk = request.user.attributes.get("dingtalk", {})
return dingtalk.get("dept_id_list", [])
```

Assign only the required SAML mappings to each SAML provider. A downstream SAML
application should receive DingTalk attributes only when its provider explicitly
includes the corresponding `SAMLPropertyMapping`.

If a SAML service provider expects a fixed attribute name such as `displayName`
or `avatar`, create a provider-specific mapping with that exact SAML attribute
name instead of reusing the generic `dingtalkName` or `dingtalkAvatar` names.

## Privacy Rule

DingTalk profile data can include personal and organizational information such
as mobile phone number, email address, job title, avatar URL, department IDs,
job number, and role data. Do not expose these values globally or by default.

Map mobile, email, title, avatar, department, role, and similar DingTalk fields
only for each downstream application that has an explicit need for them. Keep
each OIDC `ScopeMapping` or SAML `SAMLPropertyMapping` scoped to the specific
provider/application that is allowed to receive those attributes.

Do not map `dingtalk.raw_profile` to downstream applications. It is retained as
source metadata for troubleshooting and future field compatibility, and may
contain additional DingTalk fields beyond the explicit claims listed above.

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

## When A Downstream Application Should Still Connect To DingTalk

Use direct DingTalk integration only when the application needs:

- DingTalk write APIs.
- Data fresher than the configured authentik sync interval.
- DingTalk APIs outside users, departments, and manager relationships.
- Tenant-specific DingTalk permissions that should not be shared with authentik.

For current-user identity and organization context, prefer authentik claims.
For read-only directory lookup, prefer the authenticated DingTalk directory API.

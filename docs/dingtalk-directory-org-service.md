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

## Runtime Contract

The DingTalk directory service reuses the configured DingTalk OAuth source app
credentials to sync organization data into authentik-owned cache tables. Token
issuance and downstream claim mapping read from the local cache; they do not
call DingTalk live.

Directory data is scoped by DingTalk source and `corp_id`. A user from one
DingTalk source or tenant must not be merged with another source or tenant
unless a separate application-level integration explicitly handles that
relationship.

## Data Exposed To Downstream Applications

Downstream applications should receive DingTalk organization data through
provider-specific OIDC `ScopeMapping` or SAML `SAMLPropertyMapping` objects.
Only assign those mappings to applications approved to receive organization
relationship data.

Current-user organization context can include:

- DingTalk `corp_id`, `user_id`, and source slug.
- The current user's cached departments.
- Department paths when requested by the mapping.
- Direct manager and manager chain when requested by the mapping.
- Cache freshness metadata.

Default current-user mappings must not expose mobile number, email address,
raw DingTalk profile data, or full department membership.

An email address cached from DingTalk is directory contact metadata. It is not
a verified identity attribute and must not be used as a stable identity or
account-correlation key.

## Directory API

The optional DingTalk directory API is for applications that are explicitly
permitted to query read-only directory data. Use it for directory lookup needs
that are broader than current-user claims, while keeping authentik RBAC and
application ownership review in front of the data.

Directory list endpoints require access to the DingTalk OAuth source and the
matching dedicated directory permission, such as
`view_dingtalkdirectorydepartment` or `view_dingtalkdirectoryuser`. The
current-user organization endpoint may return the authenticated user's own
context without granting full directory enumeration.

The directory users endpoint is a high-privilege release surface. Its
serializer is available only when the caller has both read access to the
requested DingTalk OAuth source and
`view_dingtalkdirectoryuser`. It returns operational organization fields plus
`email`, `mobile`, and `job_number` for approved downstream directory syncs.
It deliberately excludes the cached `raw`, `union_id`, and `open_id` fields.

This directory API contract does not change the default OIDC mappings or the
current-user organization response. Those surfaces continue to omit contact
fields unless a provider-specific claim mapping is explicitly reviewed and
assigned.

## Sync And Freshness

Directory sync is read-only. It fetches departments, users, and manager
relationships from DingTalk, then updates the local cache for the relevant
source and `corp_id`.

Scheduled sync should keep the cache current for normal downstream use. Manual
sync is available for operators during rollout, tenant validation, and incident
recovery.

When the latest successful sync is older than the configured freshness
threshold, organization context should be marked stale. Stale data can still be
returned so applications can make a local policy decision without forcing token
issuance to depend on DingTalk availability.

## Rollout Checklist

1. Confirm DingTalk app OpenAPI permissions.
2. Deploy migrations.
3. Trigger manual sync for one real corp ID.
4. Verify sync status and counters.
5. Add OIDC/SAML mappings only to approved applications.
6. Confirm an unapproved application receives no DingTalk organization claims.
7. Enable scheduled sync.
8. Monitor sync errors for 24 hours.

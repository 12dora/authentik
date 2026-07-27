"""OAuth Source tasks"""

from json import dumps

from django.db import DatabaseError
from django.utils.translation import gettext_lazy as _
from dramatiq.actor import actor
from requests import RequestException
from structlog.stdlib import get_logger

from authentik.lib.utils.http import get_http_session
from authentik.sources.oauth.models import OAuthSource, UserOAuthSourceConnection
from authentik.tasks.middleware import CurrentTask

LOGGER = get_logger()


@actor(
    description=_(
        "Update OAuth sources' config from well_known, and JWKS info from the configured URL."
    )
)
def update_well_known_jwks():
    self = CurrentTask.get_task()
    session = get_http_session()
    for source in OAuthSource.objects.all().exclude(oidc_well_known_url=""):
        try:
            well_known_config = session.get(source.oidc_well_known_url)
            well_known_config.raise_for_status()
        except RequestException as exc:
            text = exc.response.text if exc.response is not None else str(exc)
            LOGGER.warning("Failed to update well_known", source=source, exc=exc, text=text)
            self.info(f"Failed to update OIDC configuration for {source.slug}")
            continue
        config: dict = well_known_config.json()
        try:
            dirty = False
            source_attr_key = (
                ("authorization_url", "authorization_endpoint"),
                ("access_token_url", "token_endpoint"),
                ("profile_url", "userinfo_endpoint"),
                ("oidc_jwks_url", "jwks_uri"),
            )
            for source_attr, config_key in source_attr_key:
                # Check if we're actually changing anything to only
                # save when something has changed
                if config_key not in config:
                    continue
                if getattr(source, source_attr, "") != config.get(config_key, ""):
                    dirty = True
                setattr(source, source_attr, config[config_key])
        except (IndexError, KeyError) as exc:
            LOGGER.warning(
                "Failed to update well_known",
                source=source,
                exc=exc,
            )
            self.info(f"Failed to update OIDC configuration for {source.slug}")
            continue
        if dirty:
            LOGGER.info("Updating sources' OpenID Configuration", source=source)
            source.save()

    for source in OAuthSource.objects.all().exclude(oidc_jwks_url=""):
        try:
            jwks_config = session.get(source.oidc_jwks_url)
            jwks_config.raise_for_status()
        except RequestException as exc:
            text = exc.response.text if exc.response is not None else str(exc)
            LOGGER.warning("Failed to update JWKS", source=source, exc=exc, text=text)
            self.info(f"Failed to update JWKS for {source.slug}")
            continue
        config = jwks_config.json()
        if dumps(source.oidc_jwks, sort_keys=True) != dumps(config, sort_keys=True):
            source.oidc_jwks = config
            LOGGER.info("Updating sources' JWKS", source=source)
            source.save()


@actor(description=_("Sync DingTalk directory cache."))
def dingtalk_directory_sync(source_pk: str, corp_id: str, run_id: str | None = None):
    source = OAuthSource.objects.filter(pk=source_pk, provider_type="dingtalk").first()
    if not source:
        from authentik.sources.oauth.dingtalk.sync import (
            DINGTALK_SYNC_ERROR_SOURCE_UNAVAILABLE,
            finalize_dingtalk_directory_sync_error,
        )

        finalize_dingtalk_directory_sync_error(
            source_pk=source_pk,
            corp_id=corp_id,
            run_id=run_id,
            error_code=DINGTALK_SYNC_ERROR_SOURCE_UNAVAILABLE,
        )
        return None
    if not source.enabled:
        from authentik.sources.oauth.dingtalk.sync import (
            DINGTALK_SYNC_ERROR_SOURCE_DISABLED,
            finalize_dingtalk_directory_sync_error,
        )

        finalize_dingtalk_directory_sync_error(
            source=source,
            corp_id=corp_id,
            run_id=run_id,
            error_code=DINGTALK_SYNC_ERROR_SOURCE_DISABLED,
        )
        return None
    from authentik.sources.oauth.dingtalk.sync import sync_dingtalk_directory

    return sync_dingtalk_directory(source, corp_id, queued_run_id=run_id)


@actor(description=_("Sync all DingTalk directory caches."))
def dingtalk_directory_sync_all():
    from authentik.sources.oauth.dingtalk.selectors import source_scoped_dingtalk_identity
    from authentik.sources.oauth.dingtalk.sync import (
        DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
        finalize_dingtalk_directory_sync_error,
        queue_dingtalk_directory_sync,
    )
    from authentik.sources.oauth.types.dingtalk import get_dingtalk_allowlist_binding

    for source in OAuthSource.objects.filter(enabled=True, provider_type="dingtalk"):
        corp_ids: set[str] = set()
        # Corps derived from users who have already logged in via this source.
        for connection in UserOAuthSourceConnection.objects.filter(source=source).select_related(
            "user"
        ):
            identity = source_scoped_dingtalk_identity(connection.user, source)
            if identity:
                corp_id, _user_id = identity
                corp_ids.add(corp_id)
        # Also seed corps configured in the allowlist so an allowed company with no logins
        # yet is pre-synced, instead of returning empty/stale until someone logs in.
        _, _, config = get_dingtalk_allowlist_binding(source, enabled_only=False)
        for company in (config or {}).get("companies", []):
            if company.get("corp_id"):
                corp_ids.add(str(company["corp_id"]))
        for corp_id in corp_ids:
            run_id = None
            try:
                run_id, should_enqueue = queue_dingtalk_directory_sync(source, corp_id)
                if should_enqueue:
                    dingtalk_directory_sync.send(str(source.pk), corp_id, str(run_id))
            except (DatabaseError, RuntimeError, ValueError) as exc:
                if run_id and isinstance(exc, RuntimeError):
                    finalize_dingtalk_directory_sync_error(
                        source=source,
                        corp_id=corp_id,
                        run_id=run_id,
                        exc=exc,
                        error_code=DINGTALK_SYNC_ERROR_BROKER_UNAVAILABLE,
                    )
                LOGGER.warning(
                    "dingtalk_directory_sync_all_corp_failed",
                    source_slug=source.slug,
                    corp_id=corp_id,
                    exception_type=type(exc).__name__,
                )

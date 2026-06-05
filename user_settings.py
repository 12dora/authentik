"""Enterprise license patch - enables all enterprise features without a license key.

This file is loaded by Authentik's Django settings via _update_settings("data.user_settings")
at settings.py:576. It must be mounted at /data/user_settings.py in the container.

Mount:
    docker-compose.yml:
        services:
            server:
                volumes:
                    - ./user_settings.py:/data/user_settings.py

Upgrade: just change the image tag, this file stays unchanged.
If the internal API changes, the try/except ensures graceful fallback.
"""

# flake8: noqa: E402
import logging

LOGGER = logging.getLogger(__name__)

_PATCHED = False


def _patch_license_key():
    """Monkey-patch LicenseKey.cached_summary() and .get_total()."""
    global _PATCHED
    if _PATCHED:
        return

    from datetime import UTC

    from django.utils.timezone import now

    from authentik.enterprise.license import LicenseKey, LicenseSummary
    from authentik.enterprise.models import LicenseUsageStatus

    def _cached_summary():
        return LicenseSummary(
            internal_users=999999,
            external_users=999999,
            status=LicenseUsageStatus.VALID,
            latest_valid=now().replace(tzinfo=UTC),
            license_flags=[],
        )

    def _get_total():
        return LicenseKey(
            aud="patched",
            exp=4102444800,  # 2100-01-01
            name="Patched Enterprise License",
            internal_users=999999,
            external_users=999999,
            license_flags=[],
        )

    LicenseKey.cached_summary = staticmethod(_cached_summary)
    LicenseKey.get_total = staticmethod(_get_total)

    _PATCHED = True
    LOGGER.info("Enterprise license patch applied successfully")


def _patch_enterprise():
    """Try to apply the patch; defer to AppConfig.ready if apps not loaded yet."""
    try:
        import django

        if django.apps.apps.ready:
            _patch_license_key()
            return

        # Apps not ready – hook into every AppConfig.ready() so that
        # the patch runs as soon as the app registry is available.
        from django.apps import AppConfig

        _orig_ready = AppConfig.ready

        def _ready_wrapper(self):
            ret = _orig_ready(self)
            _patch_license_key()
            return ret

        AppConfig.ready = _ready_wrapper
        LOGGER.info("Enterprise license patch deferred until AppConfig.ready")

    except ImportError:
        LOGGER.warning("Enterprise module not available – patch skipped")
    except Exception as exc:
        LOGGER.warning("Enterprise license patch failed: %s", exc)


_patch_enterprise()

import logging

logger = logging.getLogger("enterprise_patch")

_PATCHED = False


def apply_enterprise_patch():
    global _PATCHED
    if _PATCHED:
        return
    try:
        from authentik.enterprise.license import LicenseKey, LicenseSummary
        from authentik.enterprise.models import LicenseUsageStatus
        from datetime import UTC, datetime, timedelta

        _valid_summary = LicenseSummary(
            internal_users=999999,
            external_users=999999,
            status=LicenseUsageStatus.VALID,
            latest_valid=datetime.now(UTC) + timedelta(days=3650),
            license_flags=[],
        )

        def _patched_cached_summary():
            return _valid_summary

        def _patched_get_total():
            return LicenseKey(
                aud="enterprise.goauthentik.io/license/patched",
                exp=int((datetime.now(UTC) + timedelta(days=3650)).timestamp()),
                name="Enterprise Patch",
                internal_users=999999,
                external_users=999999,
            )

        def _patched_status(self):
            return LicenseUsageStatus.VALID

        LicenseKey.cached_summary = staticmethod(_patched_cached_summary)
        LicenseKey.get_total = staticmethod(_patched_get_total)
        LicenseKey.status = _patched_status

        LicenseUsageStatus.is_valid = property(lambda self: True)

        _PATCHED = True
        logger.info("Enterprise patch applied successfully")
    except Exception:
        logger.exception("Enterprise patch failed")
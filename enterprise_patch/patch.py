"""Development-only enterprise license patch.

The authentik Enterprise Edition license (``authentik/enterprise/LICENSE``) permits
copying and modifying the enterprise portion of the software "for development and
testing purposes, without requiring a subscription". This module exists so enterprise
code paths can be exercised in the local development stack. Running a production
instance with this patch applied still requires a valid subscription.

Hardening notes: the previous version of this patch swallowed every exception, so an
upstream refactor of the license API would turn into a silent downgrade rather than a
visible failure. This version:

* declares the upstream attributes it depends on and refuses to patch when they move,
  logging exactly which ones are missing;
* verifies the canonical gate (``LicenseKey.cached_summary().status.is_valid``) actually
  flips after patching, instead of assuming it did;
* can be fully undone, so ``authentik/custom/tests/test_enterprise_patch.py`` can assert
  the whole contract against whatever upstream currently ships.
"""

import os
import sys
from datetime import UTC, datetime

from structlog.stdlib import get_logger

LOGGER = get_logger("enterprise_patch")

#: Log marker to grep for when checking whether the patch took effect.
MARKER = "enterprise-patch"

#: Set AUTHENTIK_ENTERPRISE_PATCH=false to build/run an image without the patch.
ENV_TOGGLE = "AUTHENTIK_ENTERPRISE_PATCH"

_MISSING = object()
_ORIGINALS: dict[tuple[type, str], object] = {}
#: Mutable so helpers can flip it without `global` (ruff PLW0603).
#: ``startup`` is None until AppConfig.ready() has decided, then True (patched),
#: False (deliberately skipped). Tests assert on it because a later restore_*() call
#: would otherwise hide what happened at startup.
_STATE: dict[str, bool | None] = {"patched": False, "startup": None}


def is_enabled() -> bool:
    """Whether the patch should be applied at startup."""
    return os.environ.get(ENV_TOGGLE, "true").strip().lower() not in ("false", "0", "no", "off")


def in_test_run() -> bool:
    """Whether this process is running the test suite.

    ``settings.TEST`` cannot be used on its own: ``authentik.root.test_runner`` only sets
    it inside ``run_tests()``, long after ``django.setup()`` has called every
    ``AppConfig.ready()``. Checking argv catches the startup window; the settings flag
    covers anything asking later.
    """
    from django.conf import settings

    if getattr(settings, "TEST", False) or "pytest" in sys.modules:
        return True
    return len(sys.argv) > 1 and sys.argv[1] == "test"


def startup_result() -> bool | None:
    """What AppConfig.ready() did: True patched, False skipped, None not reached."""
    return _STATE["startup"]


def apply_at_startup() -> bool | None:
    """Entry point for AppConfig.ready(). Returns None when deliberately skipped."""
    if in_test_run():
        _STATE["startup"] = False
        return None
    if not is_enabled():
        LOGGER.info("enterprise patch disabled via environment", marker=MARKER)
        _STATE["startup"] = False
        return None
    applied = apply_enterprise_patch()
    _STATE["startup"] = applied
    return applied


def _targets():
    """Import the upstream objects this patch depends on."""
    from authentik.enterprise.license import LicenseKey, LicenseSummary
    from authentik.enterprise.models import LicenseUsageStatus

    return LicenseKey, LicenseSummary, LicenseUsageStatus


def check_upstream_contract() -> list[str]:
    """Return the upstream attributes this patch needs but cannot find.

    An empty list means the patch still matches upstream. A non-empty list after an
    upstream merge is the signal that the patch needs to be re-pointed.
    """
    try:
        license_key, license_summary, usage_status = _targets()
    except ImportError as exc:
        return [f"import failed: {exc}"]

    missing: list[str] = []
    for attr in ("cached_summary", "get_total", "status", "summary"):
        if not hasattr(license_key, attr):
            missing.append(f"LicenseKey.{attr}")
    key_fields = getattr(license_key, "__dataclass_fields__", {})
    for field in ("aud", "exp", "name", "internal_users", "external_users"):
        if field not in key_fields:
            missing.append(f"LicenseKey.{field}")
    summary_fields = getattr(license_summary, "__dataclass_fields__", {})
    for field in ("internal_users", "external_users", "status", "latest_valid", "license_flags"):
        if field not in summary_fields:
            missing.append(f"LicenseSummary.{field}")
    if not hasattr(usage_status, "VALID"):
        missing.append("LicenseUsageStatus.VALID")
    if not isinstance(usage_status.__dict__.get("is_valid"), property):
        missing.append("LicenseUsageStatus.is_valid (property)")
    return missing


def gate_is_open() -> bool:
    """Evaluate the canonical gate every enterprise feature check goes through.

    ``authentik/enterprise/{api,middleware,apps}.py`` all read
    ``LicenseKey.cached_summary().status.is_valid``.
    """
    license_key, _, _ = _targets()
    return bool(license_key.cached_summary().status.is_valid)


def _override(owner: type, name: str, value) -> None:
    """Replace an attribute, remembering the original so it can be restored."""
    _ORIGINALS.setdefault((owner, name), owner.__dict__.get(name, _MISSING))
    setattr(owner, name, value)


def apply_enterprise_patch(force: bool = False) -> bool:
    """Make the enterprise license report as valid. Returns whether the gate is open."""
    if _STATE["patched"] and not force:
        return True

    missing = check_upstream_contract()
    if missing:
        LOGGER.error(
            "enterprise patch NOT applied: upstream license API changed",
            marker=MARKER,
            missing=missing,
        )
        return False

    license_key, license_summary, usage_status = _targets()
    valid_until = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
    seats = 999999

    summary = license_summary(
        internal_users=seats,
        external_users=seats,
        status=usage_status.VALID,
        latest_valid=valid_until,
        license_flags=[],
    )

    _override(license_key, "cached_summary", staticmethod(lambda: summary))
    _override(
        license_key,
        "get_total",
        staticmethod(
            lambda: license_key(
                aud="enterprise.goauthentik.io/license/patched",
                exp=int(valid_until.timestamp()),
                name="Enterprise Patch",
                internal_users=seats,
                external_users=seats,
            )
        ),
    )
    _override(license_key, "status", lambda self: usage_status.VALID)
    _override(usage_status, "is_valid", property(lambda self: True))

    if not gate_is_open():
        LOGGER.error(
            "enterprise patch applied but the license gate still reports invalid",
            marker=MARKER,
        )
        return False

    _STATE["patched"] = True
    LOGGER.info("enterprise patch applied (development use only)", marker=MARKER)
    return True


def restore_enterprise_patch() -> None:
    """Undo the patch. Used by tests; not called at runtime."""
    for (owner, name), original in reversed(list(_ORIGINALS.items())):
        if original is _MISSING:
            delattr(owner, name)
        else:
            setattr(owner, name, original)
    _ORIGINALS.clear()
    _STATE["patched"] = False

"""Guard the development-only enterprise license patch against upstream drift.

These tests are the reason the patch cannot silently stop working after an upstream
merge: if authentik moves or renames any part of the license API the patch hooks into,
`test_upstream_contract_unchanged` fails with the exact attribute names to re-point.
"""

from django.core.cache import cache
from django.test import TestCase

from authentik.enterprise.license import CACHE_KEY_ENTERPRISE_LICENSE, LicenseKey
from enterprise_patch.patch import (
    apply_enterprise_patch,
    check_upstream_contract,
    gate_is_open,
    in_test_run,
    is_enabled,
    restore_enterprise_patch,
    startup_result,
)


class TestEnterprisePatch(TestCase):
    """Development enterprise patch"""

    def setUp(self):
        # Tests run in a shared process and in arbitrary order, so start every case from
        # a known-unpatched state rather than assuming the previous one cleaned up.
        restore_enterprise_patch()
        cache.delete(CACHE_KEY_ENTERPRISE_LICENSE)
        self.addCleanup(cache.delete, CACHE_KEY_ENTERPRISE_LICENSE)
        self.addCleanup(restore_enterprise_patch)

    def test_upstream_contract_unchanged(self):
        """Every upstream attribute the patch hooks into still exists."""
        self.assertEqual(check_upstream_contract(), [])

    def test_app_is_installed(self):
        """The patch app is registered from the repo, not from /data/user_settings.py."""
        from django.apps import apps

        self.assertTrue(apps.is_installed("enterprise_patch"))

    def test_not_applied_during_tests(self):
        """Test runs stay unlicensed so upstream's enterprise tests keep their meaning.

        Asserts on what ready() decided rather than on the live gate: setUp() restores
        the patch, which would mask a startup that wrongly applied it.
        """
        self.assertTrue(in_test_run())
        self.assertIs(startup_result(), False)
        self.assertFalse(gate_is_open())

    def test_patch_opens_the_license_gate(self):
        """Applying the patch flips the canonical gate all enterprise checks read."""
        self.addCleanup(restore_enterprise_patch)
        self.addCleanup(cache.delete, CACHE_KEY_ENTERPRISE_LICENSE)

        self.assertFalse(gate_is_open())
        self.assertTrue(apply_enterprise_patch(force=True))
        self.assertTrue(gate_is_open())
        self.assertTrue(LicenseKey.cached_summary().status.is_valid)
        self.assertGreater(LicenseKey.get_total().internal_users, 0)

    def test_restore_reverts_to_upstream_behaviour(self):
        """The patch is fully reversible, so it cannot leak into other tests."""
        apply_enterprise_patch(force=True)
        restore_enterprise_patch()
        cache.delete(CACHE_KEY_ENTERPRISE_LICENSE)
        self.assertFalse(gate_is_open())

    def test_toggle_defaults_to_enabled(self):
        """Without the env override the patch runs in the development stack."""
        self.assertTrue(is_enabled())

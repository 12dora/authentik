from django.apps import AppConfig


class EnterprisePatchConfig(AppConfig):
    name = "enterprise_patch"
    label = "enterprise_patch"
    verbose_name = "Enterprise Patch"
    default = True

    def ready(self):
        from enterprise_patch.patch import apply_at_startup

        # Skips itself during test runs: upstream's own enterprise tests assert
        # unlicensed behaviour, and test_enterprise_patch.py applies/restores explicitly.
        apply_at_startup()

from django.apps import AppConfig


class EnterprisePatchConfig(AppConfig):
    name = "enterprise_patch"
    label = "enterprise_patch"
    verbose_name = "Enterprise Patch"
    default = True

    def ready(self):
        from enterprise_patch.patch import apply_enterprise_patch

        apply_enterprise_patch()
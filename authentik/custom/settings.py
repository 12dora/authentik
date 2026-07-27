"""Custom app settings."""

TENANT_APPS = [
    "authentik.custom.easyauth",
    # Registered here rather than in /data/user_settings.py: deployments bind-mount a
    # host directory over /data, which shadows the copy baked into the image and
    # silently drops the app from INSTALLED_APPS.
    "enterprise_patch",
]

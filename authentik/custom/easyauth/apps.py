"""EasyAuth custom app config."""

from authentik.blueprints.apps import ManagedAppConfig


class EasyAuthCustomConfig(ManagedAppConfig):
    """EasyAuth-facing custom API layer."""

    name = "authentik.custom.easyauth"
    label = "authentik_custom_easyauth"
    default = True

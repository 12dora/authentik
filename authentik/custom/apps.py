"""Custom app aggregator config."""

from authentik.blueprints.apps import ManagedAppConfig


class AuthentikCustomConfig(ManagedAppConfig):
    """Aggregate locally maintained custom apps."""

    name = "authentik.custom"
    label = "authentik_custom"
    default = True

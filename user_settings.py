# Copied into the image at /data/user_settings.py and loaded by authentik as
# `data.user_settings` (authentik/root/settings.py).
#
# WARNING: deployments that bind-mount a host directory onto /data (the standard
# docker-compose layout does) shadow this file, so anything registered here silently
# disappears. That is exactly how `enterprise_patch` stopped being installed. Register
# apps in authentik/custom/settings.py instead — that lives inside the Python package
# and cannot be shadowed by a volume. This file is kept only as a hook for real
# per-deployment overrides placed in the mounted /data directory.
TENANT_APPS = ["enterprise_patch"]

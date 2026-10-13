import logging
import os
import sys
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class BuildsConfig(AppConfig):
    name = "builds"

    def ready(self):
        # Only for the actual `runserver` process — not migrate/shell/test/etc., and not
        # the autoreloader's separate watcher process (RUN_MAIN is only set in the child
        # process that actually serves requests). Revisit this gate once there's a real
        # production WSGI entrypoint (gunicorn etc.) instead of runserver.
        if "runserver" not in sys.argv:
            return
        if os.environ.get("RUN_MAIN") != "true":
            return

        threading.Thread(target=_warm_caches, daemon=True).start()


def _warm_caches() -> None:
    """Best-effort: pre-populate the vCenter/NetBox caches (see builds/vcenter_client.py,
    builds/netbox_client.py) right after the server starts, so whoever loads the build
    form first doesn't eat the ~30-40s cold vCenter connect time themselves."""
    from .netbox_client import get_all_tenant_group_names, get_all_tenant_names
    from .vcenter_client import get_latest_ubuntu_template, get_vcenter_inventory

    try:
        get_all_tenant_group_names()
        get_all_tenant_names()
        for site in ("rcd09", "sjc17"):
            get_vcenter_inventory(site)
            get_latest_ubuntu_template(site)
    except Exception:
        logger.warning("Startup cache warm-up failed (non-fatal).", exc_info=True)

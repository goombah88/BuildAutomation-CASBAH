"""Vault-backed service-account credential lookups — same approach as
/Users/chacride/Desktop/code/patching/vcenter_cluster_report.py (hvac KV v2 read against
the 'cll' mount point).
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

VAULT_MOUNT_POINT = "cll"

VCENTER_SECRET_PATHS = {
    "rcd09": "rcd09/c1/vm/rcd09-c1-vm-vcenter-01/local-admins/administrator@rcd09.c1.cll.cloud",
    "sjc17": "sjc17/c1/vm/sjc17-c1-vm-vcenter-01/local-admins/administrator@sjc17.c1.cll.cloud",
}

NETBOX_TOKEN_PATH = "global/service-accounts/service_netbox"
GITLAB_TOKEN_PATH = "global/service-accounts/service_gitlab"
# service_awx (AWX/Ansible Tower's existing AD service account, reused by choice — see
# ai_prompt_revised.txt AUTH / ACCESS CONTROL). This secret is SHARED across apps (not
# CASBAH-specific) and only has a "password" key — no "bind_dn", unlike CASBAH's other
# per-app Vault secrets — so the bind identity itself is a hardcoded constant here,
# same reasoning as get_vcenter_credentials' username-from-path-segment approach.
AD_BIND_ACCOUNT_PATH = "global/service-accounts/service_awx"
AD_BIND_ACCOUNT_DN = "service_awx@cll.cloud"


def _authenticated_client():
    """Vault client authenticated via AppRole (preferred, for a service like CASBAH) if
    VAULT_ROLE_ID/VAULT_SECRET_ID are set, otherwise falls back to the static VAULT_TOKEN.
    """
    import hvac

    client = hvac.Client(url=settings.VAULT_URL)
    if settings.VAULT_ROLE_ID and settings.VAULT_SECRET_ID:
        result = client.auth.approle.login(
            role_id=settings.VAULT_ROLE_ID,
            secret_id=settings.VAULT_SECRET_ID,
        )
        client.token = result["auth"]["client_token"]
    elif settings.VAULT_TOKEN:
        client.token = settings.VAULT_TOKEN
    else:
        raise RuntimeError(
            "No Vault credentials configured (VAULT_ROLE_ID/VAULT_SECRET_ID or VAULT_TOKEN)"
        )

    if not client.is_authenticated():
        raise RuntimeError("Vault authentication failed")
    return client


def _read_vault_secret(path: str, key: str) -> str | None:
    """Read a single key out of a KV v2 secret at `path` (under VAULT_MOUNT_POINT).
    Returns None if Vault isn't configured/reachable, or the key/secret is missing."""
    if not settings.VAULT_URL:
        return None
    if (
        not (settings.VAULT_ROLE_ID and settings.VAULT_SECRET_ID)
        and not settings.VAULT_TOKEN
    ):
        return None

    try:
        client = _authenticated_client()
        secret = client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=VAULT_MOUNT_POINT,
        )
        value = secret["data"]["data"].get(key)
        if not value:
            raise RuntimeError(f"No '{key}' key in Vault secret data at '{path}'")
        return str(value)
    except Exception:
        logger.warning("Vault secret lookup failed for path '%s'.", path, exc_info=True)
        return None


def get_vcenter_credentials(site: str) -> tuple[str, str] | tuple[None, None]:
    """Return (username, password) for the site's vCenter service account from Vault, or
    (None, None) if Vault isn't configured/reachable — callers should fall back to the
    stub template in that case. Username is the trailing segment of the secret path
    itself, not a separate key."""
    path = VCENTER_SECRET_PATHS.get(site)
    if not path:
        return None, None

    password = _read_vault_secret(path, "password")
    if not password:
        return None, None
    return path.rsplit("/", 1)[-1], password


def get_netbox_api_token() -> str | None:
    """Returns None if Vault isn't configured/reachable — callers should treat NetBox as
    unavailable in that case (see builds/netbox_client.py)."""
    return _read_vault_secret(NETBOX_TOKEN_PATH, "api_key")


def get_ad_bind_credentials() -> tuple[str, str] | tuple[None, None]:
    """Return (bind_dn, password) for the LDAP service account CASBAH uses to search
    Active Directory during login (see builds/ad_auth.py), or (None, None) if Vault
    isn't configured/reachable or the secret is missing its "password" key. Callers
    must fail closed (refuse to authenticate anyone) in that case — this gates login,
    unlike the "can't verify, don't block" fallback policy used elsewhere in this
    codebase for build-form validation. bind_dn is always AD_BIND_ACCOUNT_DN (a fixed
    constant, not read from Vault — see that constant's comment)."""
    password = _read_vault_secret(AD_BIND_ACCOUNT_PATH, "password")
    if not password:
        return None, None
    return AD_BIND_ACCOUNT_DN, password


def get_gitlab_api_token() -> str | None:
    """Returns None if Vault isn't configured/reachable. Consumed by builds/gitlab_client.py
    (GitLab automation, ai_prompt_revised.txt section 4)."""
    return _read_vault_secret(GITLAB_TOKEN_PATH, "api_token")


# Per-build CI token: short-lived rather than CASBAH's own live token (see
# get_service_vault_token) — sized to the ~10 min pipeline runtime noted in
# ai_prompt_revised.txt, with headroom for a slow run.
BUILD_PIPELINE_TOKEN_TTL = "20m"


def get_service_vault_token() -> str | None:
    """A short-lived Vault child token (same policies as CASBAH's own authenticated
    client, see _authenticated_client) for embedding into a new GitLab project's
    VAULT_TOKEN CI/CD variable, so that project's own pipeline can authenticate to
    Vault. Returns None if Vault isn't configured/reachable, or if the calling
    token/AppRole isn't permitted to create child tokens (needs the "create"
    capability on "auth/token/create" — see ai_prompt_revised.txt GOING-LIVE CHECKLIST).

    Security note: earlier versions of this handed out CASBAH's own live token
    verbatim, valid indefinitely, to every generated build repo. This version scopes
    the blast radius of a leaked CI variable down to BUILD_PIPELINE_TOKEN_TTL — but it
    still carries the SAME POLICIES as CASBAH's own token (narrowing the actual paths
    is a follow-up needing visibility into the skeleton-vm pipeline's Vault usage,
    which lives in a separate GitLab project CASBAH's service account can't read), and
    it's NOT an orphan token: confirmed live against the real Vault server (2026-09-29)
    that CASBAH's AppRole lacks the root/sudo privilege orphan tokens require. Being
    non-orphan means this child token is tied to CASBAH's own token's lifecycle —
    if CASBAH's own token/AppRole login is revoked while a build's CI pipeline is
    still running, that pipeline's Vault access dies with it. Acceptable tradeoff
    given the short TTL either way, but worth knowing if debugging a pipeline that
    suddenly loses Vault access mid-run.
    """
    if not settings.VAULT_URL:
        return None
    if (
        not (settings.VAULT_ROLE_ID and settings.VAULT_SECRET_ID)
        and not settings.VAULT_TOKEN
    ):
        return None
    try:
        client = _authenticated_client()
        parent_policies = client.auth.token.lookup_self()["data"]["policies"]
        child = client.auth.token.create(
            policies=parent_policies,
            ttl=BUILD_PIPELINE_TOKEN_TTL,
            display_name="casbah-build-pipeline",
        )
        return child["auth"]["client_token"]
    except Exception:
        logger.warning(
            "Could not mint a scoped Vault child token for CI/CD variable use "
            "(does the AppRole/token have the 'create' capability on "
            "'auth/token/create'?).",
            exc_info=True,
        )
        return None

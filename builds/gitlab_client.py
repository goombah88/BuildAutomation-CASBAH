"""GitLab REST API client for the automated build-repo pipeline (see
ai_prompt_revised.txt section "4. GITLAB REPO"). REST helpers ported from
CASBAH-PrepareNewBuild's gitlab_client.py.

Orchestration CHANGED 2026-09-29: originally used GitLab's Fork API (no local git
clone/push). Verified live against the real GitLab instance that forking is blocked
for this account/instance ("is not allowed to import projects" — GitLab implements
fork as an import operation under the hood). Switched to the same clone-locally/
create-empty-project/push approach CASBAH-PrepareNewBuild already uses (see its
build_flow.py).

CHANGED AGAIN 2026-10-08: now that CASBAH is a personal tool (not heading to prod),
clones live permanently under settings.VM_BUILDS_DIR instead of a temp dir deleted
right after the build — the same local build-copy convention
CASBAH-PrepareNewBuild already uses, so both tools share one set of on-disk copies.
skeleton-vm itself is cloned once into VM_BUILDS_DIR/skeleton-vm and pulled on later
builds rather than re-cloned from scratch every time.
"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
from pathlib import Path

import requests
from django.conf import settings

from .vault_client import (
    get_gitlab_api_token,
    get_netbox_api_token,
    get_service_vault_token,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
GIT_SUBPROCESS_TIMEOUT = 60  # seconds per git command — bounded, same reasoning as
# AD_CONNECT_TIMEOUT: a hung git clone/push must fail fast, not hang the worker forever.

SKELETON_PROJECT_PATH = "project-templates/skeleton-vm"
VMS_NAMESPACE_PATH = "infrastructure-as-code/vms"
VMS_NAMESPACE_ID = 600  # known id (see CASBAH-PrepareNewBuild/build_flow.py) — tried
# first since group ids are stable even if the path/slug changes, falls back to a path
# lookup so the two failure modes are distinguishable.
NEW_VM_TEMPLATE_FILENAME = "NEW_VM_NAME.yaml"
DEFAULT_BRANCH = "main"
DEVELOPMENT_BRANCH = "development"
PLAN_JOB_NAME = "terraform_verify_and_plan_job"
APPLY_JOB_NAME = "terraform_apply_job"


class GitLabBuildError(Exception):
    """Raised when the automated build-repo pipeline can't complete a step."""


def _api_base() -> str:
    return f"{settings.GITLAB_URL}/api/v4"


def _encoded(path: str) -> str:
    return requests.utils.quote(path, safe="")


def _request(method: str, path: str, token: str, **kwargs) -> requests.Response:
    try:
        return requests.request(
            method,
            f"{_api_base()}{path}",
            headers={"PRIVATE-TOKEN": token},
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise GitLabBuildError(f"GitLab API request to {path} failed: {exc}") from exc


def get_namespace_id(token: str) -> int:
    by_id = _request("GET", f"/groups/{VMS_NAMESPACE_ID}", token)
    if by_id.status_code == 200:
        return by_id.json()["id"]

    by_path = _request("GET", f"/groups/{_encoded(VMS_NAMESPACE_PATH)}", token)
    if by_path.status_code == 200:
        return by_path.json()["id"]

    raise GitLabBuildError(
        f"Could not resolve GitLab namespace '{VMS_NAMESPACE_PATH}' by id "
        f"({VMS_NAMESPACE_ID}) or path: by id {by_id.status_code}, by path {by_path.status_code}."
    )


def find_project(build_name: str, namespace_id: int, token: str) -> dict | None:
    """Like project_exists, but returns the matching project's own data (so callers
    can link directly to it) instead of just a bool."""
    resp = _request(
        "GET",
        "/projects",
        token,
        params={"search": build_name, "namespace_id": namespace_id},
    )
    if resp.status_code != 200:
        raise GitLabBuildError(
            f"Failed to check for an existing GitLab project: {resp.status_code} {resp.text}"
        )
    return next((p for p in resp.json() if p["name"] == build_name), None)


def project_exists(build_name: str, namespace_id: int, token: str) -> bool:
    return find_project(build_name, namespace_id, token) is not None


def build_repo_name_exists(vm_name: str) -> bool | None:
    """True/False if GitLab could answer definitively, None if Vault/GitLab is
    unreachable — callers must treat None as "couldn't verify", not "name is free"."""
    token = get_gitlab_api_token()
    if not token:
        return None
    try:
        namespace_id = get_namespace_id(token)
        return project_exists(vm_name, namespace_id, token)
    except GitLabBuildError:
        logger.warning(
            "GitLab build-repo uniqueness check failed for '%s'.",
            vm_name,
            exc_info=True,
        )
        return None


def find_build_repo_url(vm_name: str) -> str | None:
    """Direct link to the existing GitLab project's page, if vm_name already has one
    in VMS_NAMESPACE_PATH — None if it doesn't exist or GitLab/Vault is unreachable
    (same can't-verify-so-don't-block fallback as build_repo_name_exists)."""
    token = get_gitlab_api_token()
    if not token:
        return None
    try:
        namespace_id = get_namespace_id(token)
        project = find_project(vm_name, namespace_id, token)
        return project["web_url"] if project else None
    except GitLabBuildError:
        logger.warning(
            "GitLab build-repo lookup failed for '%s'.", vm_name, exc_info=True
        )
        return None


def local_build_repo_path(vm_name: str) -> Path | None:
    """Local VM_BUILDS_DIR/<vm_name> path if it already exists, else None — so callers
    can link/display it directly (see local_build_repo_exists for the plain bool)."""
    if not settings.VM_BUILDS_DIR:
        return None
    path = Path(settings.VM_BUILDS_DIR).expanduser() / vm_name
    return path if path.exists() else None


def local_build_repo_exists(vm_name: str) -> bool:
    """Whether vm_name already has a permanent local copy under settings.VM_BUILDS_DIR
    (see create_build_repo). Checked at validation time (forms.py clean_vsphere_vm_name,
    views.py validate_vm_name) so a leftover/duplicate local checkout is caught
    immediately at submit, instead of only failing deep inside the background task
    after the BuildRequest row already exists and the user has moved on to the status
    page."""
    return local_build_repo_path(vm_name) is not None


def create_project(build_name: str, namespace_id: int, token: str) -> dict:
    """Creates a new GitLab project (not a fork — see module docstring).
    initialize_with_readme=True: this GitLab instance rejects a plain `git push`
    establishing a brand-new project's default branch ("pre-receive hook declined" —
    confirmed live 2026-09-29) unless the branch already exists (also confirmed:
    force-pushing over that initial commit is separately blocked as a protected-branch
    force-push, even for Maintainers). create_build_repo instead clones this
    already-initialized project and commits skeleton-vm's content on top of the
    initial commit, so a normal (non-force) push works."""
    resp = _request(
        "POST",
        "/projects",
        token,
        data={
            "name": build_name,
            "namespace_id": namespace_id,
            # Must be the lowercase string "true", not Python's True -- requests
            # form-encodes bools via str(), producing "True" (capitalized), which
            # GitLab's Rails-based API silently doesn't recognize as truthy.
            "initialize_with_readme": "true",
        },
    )
    if resp.status_code != 201:
        raise GitLabBuildError(
            f"Failed to create GitLab project '{build_name}': {resp.status_code} {resp.text}"
        )
    return resp.json()


def get_current_username(token: str) -> str:
    """The GitLab username the token belongs to — used to build the git auth header
    (see _git_auth_args)."""
    resp = _request("GET", "/user", token)
    if resp.status_code != 200:
        raise GitLabBuildError(
            f"Failed to resolve GitLab username for git auth: "
            f"{resp.status_code} {resp.text}"
        )
    return resp.json()["username"]


def _git_auth_args(username: str, token: str) -> tuple[str, str]:
    """`-c http.extraheader=...` scopes GitLab auth to a single git invocation instead
    of embedding credentials in the remote URL, which `git` would otherwise persist in
    plaintext in .git/config — unlike the old throwaway-temp-dir approach, clones under
    settings.VM_BUILDS_DIR are now permanent (see create_build_repo), so a credential
    baked into the URL would sit on disk indefinitely instead of being deleted moments
    later."""
    credentials = base64.b64encode(f"{username}:{token}".encode()).decode()
    return ("-c", f"http.extraheader=Authorization: Basic {credentials}")


def _redact(text: str, token: str | None) -> str:
    """Strips a raw token out of git/subprocess output before it ever reaches a
    GitLabBuildError message — those get stored in BuildRequest.error_detail and
    shown on the build-status page, so a leaked token there would be a real
    credential exposure, not just a log line."""
    return text.replace(token, "***") if token else text


def _run_git(*args: str, cwd: str, redact: str | None = None) -> None:
    """Runs `git <args>` in `cwd`, raising GitLabBuildError (with `redact` — normally
    the GitLab token — scrubbed from the message) on a non-zero exit or a timeout.
    GIT_SUBPROCESS_TIMEOUT bounds this the same way AD_CONNECT_TIMEOUT bounds LDAP
    connects — a hung clone/push must fail fast, not hang the worker forever."""
    joined = " ".join(args)
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitLabBuildError(
            f"'git {_redact(joined, redact)}' timed out after {GIT_SUBPROCESS_TIMEOUT}s"
        ) from exc
    if result.returncode != 0:
        raise GitLabBuildError(
            f"'git {_redact(joined, redact)}' failed:\n"
            f"{_redact(result.stderr.strip(), redact)}"
        )


def set_ci_variable(
    project_id: int, token: str, key: str, value: str, masked: bool = False
) -> None:
    # requests form-encodes a Python bool as the literal string "True"/"False"
    # (capitalized) via str() -- GitLab's Rails-based API only recognizes lowercase
    # "true"/"TRUE" as truthy (confirmed live 2026-09-29: initialize_with_readme=True
    # was silently ignored the same way), so masked=True would have silently created
    # an UNMASKED variable. Always send explicit lowercase strings for booleans here.
    resp = _request(
        "POST",
        f"/projects/{project_id}/variables",
        token,
        data={
            "key": key,
            "value": value,
            "protected": "false",
            "masked": "true" if masked else "false",
        },
    )
    if resp.status_code == 201:
        return
    if masked and resp.status_code == 400:
        # Value doesn't meet GitLab's masking requirements (single line, no whitespace,
        # min length, limited charset) — fall back to unmasked rather than aborting the
        # whole build over a display-only protection.
        logger.warning(
            "CI/CD variable '%s' couldn't be masked (%s); creating it unmasked instead.",
            key,
            resp.text,
        )
        resp = _request(
            "POST",
            f"/projects/{project_id}/variables",
            token,
            data={"key": key, "value": value, "protected": "false", "masked": "false"},
        )
        if resp.status_code == 201:
            return
    raise GitLabBuildError(
        f"Failed to create CI/CD variable '{key}': {resp.status_code} {resp.text}"
    )


def merge_request_url(project_web_url: str) -> str:
    return (
        f"{project_web_url}/-/merge_requests/new"
        f"?merge_request%5Bsource_branch%5D={DEVELOPMENT_BRANCH}"
        f"&merge_request%5Btarget_branch%5D={DEFAULT_BRANCH}"
    )


def merge_development_branch(project_id: int, token: str) -> dict:
    """Merges development into main via the Merge Requests API (reuses an existing
    open MR if builds.tasks.advance_pipelines already created one on an earlier poll,
    otherwise creates one first). Callers must only call this once development's own
    plan pipeline has already succeeded (see advance_pipelines) — unlike
    get_latest_pipeline/get_pipeline_jobs above, this actually mutates GitLab state,
    so it raises GitLabBuildError on failure rather than swallowing it."""
    resp = _request(
        "GET",
        f"/projects/{project_id}/merge_requests",
        token,
        params={
            "source_branch": DEVELOPMENT_BRANCH,
            "target_branch": DEFAULT_BRANCH,
            "state": "opened",
        },
    )
    if resp.status_code != 200:
        raise GitLabBuildError(
            f"Failed to look up open merge requests for project {project_id}: "
            f"{resp.status_code} {resp.text}"
        )
    open_mrs = resp.json()
    if open_mrs:
        merge_iid = open_mrs[0]["iid"]
    else:
        create_resp = _request(
            "POST",
            f"/projects/{project_id}/merge_requests",
            token,
            data={
                "source_branch": DEVELOPMENT_BRANCH,
                "target_branch": DEFAULT_BRANCH,
                "title": "CASBAH: configure build",
                "remove_source_branch": "false",
            },
        )
        if create_resp.status_code != 201:
            raise GitLabBuildError(
                f"Failed to create merge request for project {project_id}: "
                f"{create_resp.status_code} {create_resp.text}"
            )
        merge_iid = create_resp.json()["iid"]

    merge_resp = _request(
        "PUT",
        f"/projects/{project_id}/merge_requests/{merge_iid}/merge",
        token,
    )
    if merge_resp.status_code != 200:
        raise GitLabBuildError(
            f"Failed to merge development into main for project {project_id} "
            f"(MR !{merge_iid}): {merge_resp.status_code} {merge_resp.text}"
        )
    return merge_resp.json()


def play_job(project_id: int, job_id: int, token: str) -> None:
    """Plays (starts) a `when: manual` job — used to auto-trigger terraform_apply_job
    once main's post-merge plan job succeeds (see builds.tasks.advance_pipelines).
    Mutates GitLab state, so raises GitLabBuildError on failure rather than
    swallowing it (same reasoning as merge_development_branch above)."""
    resp = _request("POST", f"/projects/{project_id}/jobs/{job_id}/play", token)
    if resp.status_code != 200:
        raise GitLabBuildError(
            f"Failed to play job {job_id} for project {project_id}: "
            f"{resp.status_code} {resp.text}"
        )


def get_project(project_id: int, token: str) -> dict:
    """Fetches an existing GitLab project's own metadata (used by
    clone_existing_build_repo to get its real http_url_to_repo rather than guessing
    one by string-munging the stored project_web_url)."""
    resp = _request("GET", f"/projects/{project_id}", token)
    if resp.status_code != 200:
        raise GitLabBuildError(
            f"Failed to look up GitLab project {project_id}: "
            f"{resp.status_code} {resp.text}"
        )
    return resp.json()


def clone_existing_build_repo(vm_name: str, project_id: int) -> Path:
    """Clones an ALREADY-EXISTING GitLab project (one create_build_repo made in an
    earlier run) into settings.VM_BUILDS_DIR/<vm_name> — for recreating a build's
    permanent local copy for builds made before CASBAH started keeping one at all, or
    after VM_BUILDS_DIR itself was wiped (see builds/management/commands/
    sync_local_build_repos.py). Purely a `git clone` of what's already there — never
    recreates or otherwise mutates the GitLab project itself."""
    if "/" in vm_name or "\\" in vm_name or vm_name in (".", ".."):
        raise GitLabBuildError(f"Invalid vm_name (path traversal risk): {vm_name!r}")
    if not settings.VM_BUILDS_DIR:
        raise GitLabBuildError(
            "VM_BUILDS_DIR is not configured (see ~/.api_variables_env)."
        )
    token = get_gitlab_api_token()
    if not token:
        raise GitLabBuildError("No GitLab API token available (Vault unreachable?).")
    if not shutil.which("git"):
        raise GitLabBuildError(
            "'git' executable not found on PATH — required for the clone."
        )

    base_dir = Path(settings.VM_BUILDS_DIR).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)
    build_dir = base_dir / vm_name
    if build_dir.exists():
        raise GitLabBuildError(f"'{build_dir}' already exists — nothing to recreate.")

    project = get_project(project_id, token)
    username = get_current_username(token)
    auth_args = _git_auth_args(username, token)
    _run_git(
        *auth_args,
        "clone",
        project["http_url_to_repo"],
        str(build_dir),
        cwd=str(base_dir),
        redact=token,
    )
    return build_dir


def create_build_repo(vm_name: str, yaml_text: str) -> dict:
    """Runs the full GitLab side of a build: create the vm_name project (README-
    initialized so it has a real default branch — see create_project), clone it plus
    skeleton-vm locally, layer skeleton-vm's content on top of the initial commit and
    push 'main' (this replaces GitLab's Fork API — see module docstring for why), set
    CI/CD variables, then rename/write the generated build yaml and push
    'development'. Returns {"project_id": ..., "project_web_url": ...,
    "merge_request_url": ...}. Raises GitLabBuildError on any failure — callers must
    surface build_request.error_detail and never attempt to silently roll back
    whatever step already succeeded (see ai_prompt_revised.txt ERROR HANDLING policy).

    Clones live under settings.VM_BUILDS_DIR/<vm_name> permanently (see module
    docstring) rather than being deleted after the build — on a failure, whatever
    got created on disk is left in place for inspection, same "no silent rollback"
    policy already used for the GitLab/NetBox API state itself.
    """
    # Defense-in-depth against path traversal: vm_name becomes a literal filename
    # below ("{vm_name}.yaml") — reject anything that isn't a plain path segment
    # even if a caller bypasses ServerBuildForm's own (stricter) validation.
    if "/" in vm_name or "\\" in vm_name or vm_name in (".", ".."):
        raise GitLabBuildError(f"Invalid vm_name (path traversal risk): {vm_name!r}")

    if not settings.VM_BUILDS_DIR:
        raise GitLabBuildError(
            "VM_BUILDS_DIR is not configured (see ~/.api_variables_env)."
        )

    token = get_gitlab_api_token()
    if not token:
        raise GitLabBuildError("No GitLab API token available (Vault unreachable?).")
    if not shutil.which("git"):
        raise GitLabBuildError(
            "'git' executable not found on PATH — required for the clone/push build "
            "pipeline."
        )

    base_dir = Path(settings.VM_BUILDS_DIR).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)

    build_dir = base_dir / vm_name
    if build_dir.exists():
        raise GitLabBuildError(
            f"'{build_dir}' already exists -- remove it manually before retrying "
            "this build name (CASBAH's local build copies are permanent, not "
            "throwaway, so it won't overwrite an existing one)."
        )

    namespace_id = get_namespace_id(token)
    if project_exists(vm_name, namespace_id, token):
        raise GitLabBuildError(
            f"A GitLab project named '{vm_name}' already exists in '{VMS_NAMESPACE_PATH}'."
        )

    username = get_current_username(token)
    auth_args = _git_auth_args(username, token)

    # skeleton-vm is cloned once into VM_BUILDS_DIR and pulled on later builds,
    # rather than re-cloned from scratch every time (see module docstring).
    skeleton_dir = base_dir / "skeleton-vm"
    skeleton_url = f"{settings.GITLAB_URL}/{SKELETON_PROJECT_PATH}.git"
    if skeleton_dir.is_dir():
        # A pre-existing checkout (e.g. one made by hand, or by CASBAH-PrepareNewBuild)
        # may have an SSH remote instead of HTTPS -- _git_auth_args only works over
        # HTTPS, and this background worker has no guarantee of SSH-agent access, so
        # force the remote to HTTPS first rather than silently falling back to SSH.
        _run_git("remote", "set-url", "origin", skeleton_url, cwd=str(skeleton_dir))
        _run_git(*auth_args, "pull", cwd=str(skeleton_dir), redact=token)
    else:
        _run_git(
            *auth_args,
            "clone",
            skeleton_url,
            str(skeleton_dir),
            cwd=str(base_dir),
            redact=token,
        )

    project = create_project(vm_name, namespace_id, token)
    project_id = project["id"]
    project_web_url = project["web_url"]
    if project["name"] != vm_name:
        raise GitLabBuildError(
            f"Created project name '{project['name']}' doesn't match vm_name "
            f"'{vm_name}' — CONFIG_FILE must match the project name exactly for "
            "the pipeline to trigger."
        )

    # Clone the just-created project (create_project's initialize_with_readme
    # already gave it a real, protected main branch — see that function's
    # docstring) so we commit skeleton-vm's content ON TOP of that initial
    # commit and can push normally, without needing (and likely being denied)
    # a force-push over a protected branch.
    build_dir.mkdir()
    _run_git(
        *auth_args,
        "clone",
        project["http_url_to_repo"],
        ".",
        cwd=str(build_dir),
        redact=token,
    )

    # skeleton_dir's own .git is excluded -- it's a persistent, reused checkout
    # (see above), not something build_dir should inherit as its own history.
    shutil.copytree(
        skeleton_dir,
        build_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git"),
    )

    _run_git(
        "config", "user.email", "casbah@cll.cloud", cwd=str(build_dir), redact=token
    )
    _run_git("config", "user.name", "CASBAH", cwd=str(build_dir), redact=token)
    _run_git("add", "-A", cwd=str(build_dir), redact=token)
    _run_git(
        "commit",
        "-m",
        f"Initial commit from skeleton-vm for {vm_name}",
        cwd=str(build_dir),
        redact=token,
    )
    _run_git(
        *auth_args,
        "push",
        "-u",
        "origin",
        DEFAULT_BRANCH,
        cwd=str(build_dir),
        redact=token,
    )

    set_ci_variable(project_id, token, "CONFIG_FILE", f"{vm_name}.yaml")

    netbox_token = get_netbox_api_token()
    if netbox_token:
        set_ci_variable(project_id, token, "NETBOX_TOKEN", netbox_token, masked=True)
    else:
        logger.warning(
            "No NetBox token available; skipping NETBOX_TOKEN CI/CD variable for '%s'.",
            vm_name,
        )

    vault_token = get_service_vault_token()
    if vault_token:
        set_ci_variable(project_id, token, "VAULT_TOKEN", vault_token, masked=True)
    else:
        logger.warning(
            "No Vault token available; skipping VAULT_TOKEN CI/CD variable for '%s'.",
            vm_name,
        )

    _run_git("checkout", "-b", DEVELOPMENT_BRANCH, cwd=str(build_dir), redact=token)
    old_yaml = build_dir / NEW_VM_TEMPLATE_FILENAME
    if not old_yaml.exists():
        raise GitLabBuildError(
            f"{NEW_VM_TEMPLATE_FILENAME} not found in the skeleton-vm template."
        )
    old_yaml.unlink()
    (build_dir / f"{vm_name}.yaml").write_text(yaml_text)
    _run_git("add", ".", cwd=str(build_dir), redact=token)
    _run_git(
        "commit",
        "-m",
        f"Configure build for {vm_name}.yaml",
        cwd=str(build_dir),
        redact=token,
    )
    _run_git(
        *auth_args,
        "push",
        "--set-upstream",
        "origin",
        DEVELOPMENT_BRANCH,
        cwd=str(build_dir),
        redact=token,
    )

    return {
        "project_id": project_id,
        "project_web_url": project_web_url,
        "merge_request_url": merge_request_url(project_web_url),
    }


# Statuses GitLab itself never transitions out of once reached — used to decide when
# builds/views.build_pipeline_status can stop polling (see ai_prompt_revised.txt
# "pipeline monitoring", no longer out of scope as of 2026-09-29).
PIPELINE_TERMINAL_STATUSES = {"success", "failed", "canceled", "skipped"}


def get_latest_pipeline(
    project_id: int, token: str, ref: str | None = None
) -> dict | None:
    """Most recent pipeline for `project_id`, optionally filtered to one branch/ref
    (used by builds.tasks.advance_pipelines to look at development's and main's
    pipelines separately), or None if there isn't one yet or GitLab is unreachable —
    this is display-only monitoring, so "can't verify" must never raise/block anything
    (same fallback policy as the dropdown-data lookups in netbox_client.py/
    vcenter_client.py, unlike create_build_repo above). Returns the raw GitLab
    pipeline dict (id/status/ref/web_url/created_at/...)."""
    try:
        params = {"order_by": "id", "sort": "desc", "per_page": 1}
        if ref:
            params["ref"] = ref
        resp = _request(
            "GET",
            f"/projects/{project_id}/pipelines",
            token,
            params=params,
        )
        if resp.status_code != 200:
            raise GitLabBuildError(
                f"Failed to list pipelines for project {project_id}: "
                f"{resp.status_code} {resp.text}"
            )
        pipelines = resp.json()
        return pipelines[0] if pipelines else None
    except GitLabBuildError:
        logger.warning(
            "Failed to fetch latest pipeline for project %s.", project_id, exc_info=True
        )
        return None


def get_pipeline_jobs(project_id: int, pipeline_id: int, token: str) -> list[dict]:
    """Jobs (name/stage/status/web_url) for one pipeline, oldest stage first — e.g.
    terraform_verify_and_plan_job then terraform_apply_job (see skeleton-vm's
    .gitlab-ci.yml). Returns [] if GitLab is unreachable (display-only, same fallback
    policy as get_latest_pipeline above)."""
    try:
        resp = _request(
            "GET",
            f"/projects/{project_id}/pipelines/{pipeline_id}/jobs",
            token,
        )
        if resp.status_code != 200:
            raise GitLabBuildError(
                f"Failed to list jobs for pipeline {pipeline_id}: "
                f"{resp.status_code} {resp.text}"
            )
        return list(reversed(resp.json()))
    except GitLabBuildError:
        logger.warning(
            "Failed to fetch jobs for pipeline %s (project %s).",
            pipeline_id,
            project_id,
            exc_info=True,
        )
        return []

"""Recreates missing local build-repo copies under settings.VM_BUILDS_DIR for existing
BuildRequests whose GitLab project already exists but whose local checkout is missing
(builds made before CASBAH started keeping a permanent local copy at all — see
builds/gitlab_client.py create_build_repo, or VM_BUILDS_DIR itself was wiped/moved).
Purely clones what's already on GitLab — never recreates or mutates the project
itself. Run:

    python manage.py sync_local_build_repos
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from builds.gitlab_client import GitLabBuildError, clone_existing_build_repo
from builds.models import BuildRequest


class Command(BaseCommand):
    help = "Clone any missing local build-repo copies under VM_BUILDS_DIR from GitLab"

    def handle(self, *args, **options):
        if not settings.VM_BUILDS_DIR:
            self.stderr.write(
                self.style.ERROR(
                    "VM_BUILDS_DIR is not configured (see ~/.api_variables_env)."
                )
            )
            return
        base_dir = Path(settings.VM_BUILDS_DIR).expanduser()

        recreated = skipped = failed = 0
        for build_request in BuildRequest.objects.exclude(
            gitlab_project_id=None
        ).order_by("id"):
            if (base_dir / build_request.vm_name).exists():
                skipped += 1
                continue
            try:
                clone_existing_build_repo(
                    build_request.vm_name, build_request.gitlab_project_id
                )
            except GitLabBuildError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"{build_request.vm_name}: {exc}"))
                continue
            recreated += 1
            self.stdout.write(self.style.SUCCESS(f"{build_request.vm_name}: cloned"))

        self.stdout.write(
            f"Done: {recreated} cloned, {skipped} already present, {failed} failed."
        )

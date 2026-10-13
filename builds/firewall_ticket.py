"""Files a "standard build firewall ticket" in Jira once a build's GitLab repo + NetBox
VM have been created (see builds/tasks.py run_build_pipeline): builds the firewall
request spreadsheet for the build's site (builds/firewall_request.py) and files it as a
Jira ticket in the LABS project (fields cloned from the LABS-16495 template, same as
CASBAH-PrepareNewBuild's firewall_ticket.py), attaching the spreadsheet. Uses the
requester's own personal Jira credentials — this is a personal tool, so tickets are
always reported AND assigned to whoever ran the build, not a shared queue.
"""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path
from urllib.parse import quote

from django.conf import settings

from . import jira_client
from .firewall_request import (
    FirewallRequestError,
    build_filename,
    build_workbook,
    site_config,
)

# Values copied from the LABS-16495 template ticket (see CASBAH-PrepareNewBuild's
# firewall_ticket.py — same Jira project/fields, ported here for CASBAH's own use).
PROJECT_KEY = "LABS"
ISSUE_TYPE_ID = "10004"  # Task
PRIORITY_ID = "10004"  # Minor-P4
COMPONENT_ID = "12266"  # Security
LABELS = ["Firewall_Request"]
TEAM_FIELD_ID = "customfield_10001"
TEAM_ID = (
    "84cdfe61-8341-466d-9a19-c618a10ca35f"  # Lab Infrastructure Team - Ray Viscaina
)
START_DATE_FIELD_ID = "customfield_10015"
SPRINT_FIELD_ID = "customfield_10020"
SPRINT_BOARD_ID = 618  # LABSIS Current Sprint - All Teams


class FirewallTicketError(Exception):
    """Raised when a standard build firewall ticket can't be filed."""


def _netbox_vm_search_url(hostname: str) -> str:
    """Link to the NetBox VM list filtered by hostname, rather than a specific VM
    detail page — the VM record may not be visible yet when the ticket is filed."""
    return (
        f"{settings.NETBOX_URL}/virtualization/virtual-machines/?name={quote(hostname)}"
    )


def _build_description(hostname: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": f"Standard firewall request for a new build - {hostname}",
                    }
                ],
            },
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "NetBox: "},
                    {
                        "type": "text",
                        "text": _netbox_vm_search_url(hostname),
                        "marks": [
                            {
                                "type": "link",
                                "attrs": {"href": _netbox_vm_search_url(hostname)},
                            }
                        ],
                    },
                ],
            },
        ],
    }


def _next_business_due_date() -> str:
    today = datetime.date.today()
    # Friday (weekday 4) rolls over to the following Monday.
    due = today + datetime.timedelta(days=3 if today.weekday() == 4 else 1)
    return due.isoformat()


def create_standard_build_ticket(
    hostname: str,
    site: str,
    ip: str,
    reason: str,
    email: str,
    token: str,
) -> str:
    """Build the firewall request spreadsheet and file it as a Jira ticket, returning
    the created ticket's browse URL. Raises FirewallTicketError on any failure
    (unsupported site, Jira API errors, etc.)."""
    try:
        site_cfg = site_config(site)
    except FirewallRequestError as exc:
        raise FirewallTicketError(str(exc)) from exc

    wb = build_workbook(hostname, ip, reason, site_cfg)
    filename = build_filename(hostname, site_cfg)

    session = jira_client.build_session(email, token)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / filename
            wb.save(filepath)

            # Personal tool, one user -- the ticket is always reported by and
            # assigned to whoever ran the build, no separate assignee/queue.
            account_id = jira_client.get_account_id(session, email)
            sprint_id = jira_client.get_active_sprint_id(session, SPRINT_BOARD_ID)
            today = datetime.date.today().isoformat()

            fields = {
                "project": {"key": PROJECT_KEY},
                "issuetype": {"id": ISSUE_TYPE_ID},
                "summary": f"Firewall Request - {site_cfg['label']} - {hostname}",
                "description": _build_description(hostname),
                "priority": {"id": PRIORITY_ID},
                "labels": LABELS,
                "components": [{"id": COMPONENT_ID}],
                "assignee": {"accountId": account_id},
                "reporter": {"accountId": account_id},
                "duedate": _next_business_due_date(),
                START_DATE_FIELD_ID: today,
                SPRINT_FIELD_ID: sprint_id,
                TEAM_FIELD_ID: TEAM_ID,
            }
            issue_key = jira_client.create_issue(session, fields)
            jira_client.attach_file(session, issue_key, filepath)
    except jira_client.JiraError as exc:
        raise FirewallTicketError(str(exc)) from exc

    return f"{settings.JIRA_URL}/browse/{issue_key}"

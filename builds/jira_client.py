"""Jira Cloud REST API (v3) helper functions for filing standard-build firewall
tickets (see builds/firewall_ticket.py). Ported from CASBAH-PrepareNewBuild's
jira_client.py — uses the requester's own personal Jira credentials (email + API
token), since Jira has no CASBAH-specific service account.
"""

from __future__ import annotations

from pathlib import Path

import requests
from django.conf import settings


class JiraError(Exception):
    """Raised for any Jira REST API failure."""


def build_session(email: str, token: str) -> requests.Session:
    session = requests.Session()
    session.auth = (email, token)
    session.headers.update({"Accept": "application/json"})
    return session


def get_account_id(session: requests.Session, email: str) -> str:
    resp = session.get(
        f"{settings.JIRA_URL}/rest/api/3/user/search", params={"query": email}
    )
    if not resp.ok:
        raise JiraError(
            f"Jira user lookup for '{email}' failed: {resp.status_code} {resp.text}"
        )
    results = resp.json()
    if not results:
        raise JiraError(f"No Jira user found for '{email}'.")
    return results[0]["accountId"]


def get_active_sprint_id(session: requests.Session, board_id: int) -> int:
    resp = session.get(
        f"{settings.JIRA_URL}/rest/agile/1.0/board/{board_id}/sprint",
        params={"state": "active"},
    )
    if not resp.ok:
        raise JiraError(
            f"Sprint lookup on board {board_id} failed: {resp.status_code} {resp.text}"
        )
    sprints = resp.json().get("values", [])
    if not sprints:
        raise JiraError(f"No active sprint found on board {board_id}.")
    return sprints[0]["id"]


def create_issue(session: requests.Session, fields: dict) -> str:
    resp = session.post(
        f"{settings.JIRA_URL}/rest/api/3/issue", json={"fields": fields}
    )
    if not resp.ok:
        raise JiraError(f"Failed to create ticket: {resp.status_code} {resp.text}")
    return resp.json()["key"]


def attach_file(session: requests.Session, issue_key: str, filepath: Path) -> None:
    with open(filepath, "rb") as fh:
        resp = session.post(
            f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}/attachments",
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (Path(filepath).name, fh)},
        )
    if not resp.ok:
        raise JiraError(
            f"Ticket {issue_key} was created, but attaching the spreadsheet failed: "
            f"{resp.status_code} {resp.text}"
        )

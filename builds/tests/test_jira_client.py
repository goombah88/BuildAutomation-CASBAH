from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from builds import jira_client


@override_settings(JIRA_URL="https://jira.example")
class BuildSessionTests(SimpleTestCase):
    def test_sets_basic_auth_and_accept_header(self):
        session = jira_client.build_session("user@example.com", "tok")
        self.assertEqual(session.auth, ("user@example.com", "tok"))
        self.assertEqual(session.headers["Accept"], "application/json")


@override_settings(JIRA_URL="https://jira.example")
class GetAccountIdTests(SimpleTestCase):
    def test_returns_account_id_on_success(self):
        session = MagicMock()
        session.get.return_value = MagicMock(
            ok=True, json=lambda: [{"accountId": "abc123"}]
        )
        result = jira_client.get_account_id(session, "user@example.com")
        self.assertEqual(result, "abc123")
        session.get.assert_called_once_with(
            "https://jira.example/rest/api/3/user/search",
            params={"query": "user@example.com"},
        )

    def test_raises_when_no_results(self):
        session = MagicMock()
        session.get.return_value = MagicMock(ok=True, json=lambda: [])
        with self.assertRaises(jira_client.JiraError):
            jira_client.get_account_id(session, "user@example.com")

    def test_raises_on_non_ok_response(self):
        session = MagicMock()
        session.get.return_value = MagicMock(ok=False, status_code=500, text="boom")
        with self.assertRaises(jira_client.JiraError):
            jira_client.get_account_id(session, "user@example.com")


@override_settings(JIRA_URL="https://jira.example")
class GetActiveSprintIdTests(SimpleTestCase):
    def test_returns_sprint_id_on_success(self):
        session = MagicMock()
        session.get.return_value = MagicMock(
            ok=True, json=lambda: {"values": [{"id": 42}]}
        )
        self.assertEqual(jira_client.get_active_sprint_id(session, 618), 42)

    def test_raises_when_no_active_sprint(self):
        session = MagicMock()
        session.get.return_value = MagicMock(ok=True, json=lambda: {"values": []})
        with self.assertRaises(jira_client.JiraError):
            jira_client.get_active_sprint_id(session, 618)


@override_settings(JIRA_URL="https://jira.example")
class CreateIssueTests(SimpleTestCase):
    def test_returns_issue_key_on_success(self):
        session = MagicMock()
        session.post.return_value = MagicMock(ok=True, json=lambda: {"key": "LABS-1"})
        self.assertEqual(jira_client.create_issue(session, {"project": {}}), "LABS-1")

    def test_raises_on_failure(self):
        session = MagicMock()
        session.post.return_value = MagicMock(ok=False, status_code=400, text="bad")
        with self.assertRaises(jira_client.JiraError):
            jira_client.create_issue(session, {"project": {}})


@override_settings(JIRA_URL="https://jira.example")
class AttachFileTests(SimpleTestCase):
    def test_raises_on_failure(self):
        import tempfile
        from pathlib import Path

        session = MagicMock()
        session.post.return_value = MagicMock(ok=False, status_code=500, text="boom")
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "file.xlsx"
            filepath.write_bytes(b"data")
            with self.assertRaises(jira_client.JiraError):
                jira_client.attach_file(session, "LABS-1", filepath)

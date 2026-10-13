from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from builds.firewall_ticket import FirewallTicketError, create_standard_build_ticket
from builds.jira_client import JiraError


@override_settings(JIRA_URL="https://jira.example", NETBOX_URL="https://netbox.example")
class CreateStandardBuildTicketTests(SimpleTestCase):
    def test_unknown_site_raises_firewall_ticket_error(self):
        with self.assertRaises(FirewallTicketError):
            create_standard_build_ticket(
                hostname="made-up-01",
                site="made-up-site",
                ip="10.0.0.1",
                reason="standard build",
                email="user@example.com",
                token="tok",
            )

    @patch("builds.firewall_ticket.jira_client.attach_file")
    @patch("builds.firewall_ticket.jira_client.create_issue")
    @patch("builds.firewall_ticket.jira_client.get_active_sprint_id")
    @patch("builds.firewall_ticket.jira_client.get_account_id")
    @patch("builds.firewall_ticket.jira_client.build_session")
    def test_happy_path_returns_browse_url(
        self,
        mock_build_session,
        mock_get_account_id,
        mock_get_sprint,
        mock_create_issue,
        mock_attach_file,
    ):
        mock_build_session.return_value = MagicMock()
        mock_get_account_id.return_value = "account-123"
        mock_get_sprint.return_value = 618
        mock_create_issue.return_value = "LABS-42"

        url = create_standard_build_ticket(
            hostname="rcd09-d1-testvm01",
            site="rcd09",
            ip="10.192.1.5",
            reason="standard build",
            email="user@example.com",
            token="tok",
        )

        self.assertEqual(url, "https://jira.example/browse/LABS-42")
        mock_create_issue.assert_called_once()
        fields = mock_create_issue.call_args.args[1]
        self.assertEqual(fields["assignee"], {"accountId": "account-123"})
        self.assertEqual(fields["reporter"], {"accountId": "account-123"})
        mock_attach_file.assert_called_once()

    @patch("builds.firewall_ticket.jira_client.get_account_id")
    @patch("builds.firewall_ticket.jira_client.build_session")
    def test_jira_error_is_wrapped_in_firewall_ticket_error(
        self, mock_build_session, mock_get_account_id
    ):
        mock_build_session.return_value = MagicMock()
        mock_get_account_id.side_effect = JiraError("no such user")

        with self.assertRaises(FirewallTicketError):
            create_standard_build_ticket(
                hostname="rcd09-d1-testvm01",
                site="rcd09",
                ip="10.192.1.5",
                reason="standard build",
                email="user@example.com",
                token="tok",
            )

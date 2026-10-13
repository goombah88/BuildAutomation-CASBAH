from django.test import SimpleTestCase

from builds.firewall_request import (
    FirewallRequestError,
    build_filename,
    build_workbook,
    site_config,
)


class SiteConfigTests(SimpleTestCase):
    def test_known_sites(self):
        self.assertEqual(site_config("rcd09")["label"], "RCD")
        self.assertEqual(site_config("sjc17")["label"], "SJC")

    def test_unknown_site_raises(self):
        with self.assertRaises(FirewallRequestError):
            site_config("made-up-site")


class BuildWorkbookTests(SimpleTestCase):
    def test_fills_in_marker_cells(self):
        site = site_config("rcd09")
        wb = build_workbook("rcd09-d1-testvm01", "10.192.1.5", "standard build", site)
        ws = wb.active
        values = [cell.value for row in ws.iter_rows() for cell in row]
        self.assertIn("rcd09-d1-testvm01", values)
        self.assertIn("10.192.1.5", values)
        self.assertIn("standard build", values)
        self.assertNotIn("HOSTNAME", values)
        self.assertNotIn("IP", values)
        self.assertNotIn("REASON", values)


class BuildFilenameTests(SimpleTestCase):
    def test_includes_site_label_and_hostname(self):
        site = site_config("sjc17")
        filename = build_filename("sjc17-d1-testvm01", site)
        self.assertEqual(
            filename, "SJC_New_Build_sjc17-d1-testvm01_Firewall Request Form.xlsx"
        )

from django.test import TestCase

from builds.models import BuildRequest


def make_build_request(**overrides):
    defaults = {
        "vm_name": "rcd09-d1-testvm01",
        "site": BuildRequest.Site.RCD09,
        "ipv4_address": "10.0.0.5",
        "tenant": "VMCloud Dev",
    }
    defaults.update(overrides)
    return BuildRequest.objects.create(**defaults)


class BuildRequestModelTests(TestCase):
    def test_str_includes_name_and_status_display(self):
        build_request = make_build_request()
        self.assertEqual(str(build_request), "rcd09-d1-testvm01 (Pending)")

    def test_defaults(self):
        build_request = make_build_request()
        self.assertEqual(build_request.status, BuildRequest.Status.PENDING)
        self.assertEqual(build_request.tenant_group, "HTD")
        self.assertEqual(build_request.ipv4_prefix_length, 24)
        self.assertEqual(build_request.num_cpus, 2)
        self.assertEqual(build_request.memory_gb, 4)
        self.assertTrue(build_request.firewall_ticket_requested)
        self.assertEqual(build_request.form_data, {})

    def test_is_terminal_false_for_in_progress_states(self):
        for status in (
            BuildRequest.Status.PENDING,
            BuildRequest.Status.GITLAB_IN_PROGRESS,
            BuildRequest.Status.NETBOX_IN_PROGRESS,
            BuildRequest.Status.PLAN_IN_PROGRESS,
            BuildRequest.Status.APPLY_IN_PROGRESS,
        ):
            build_request = make_build_request(
                vm_name=f"rcd09-d1-{status}", status=status
            )
            self.assertFalse(build_request.is_terminal, status)

    def test_is_terminal_true_for_final_states(self):
        for status in (
            BuildRequest.Status.COMPLETE,
            BuildRequest.Status.GITLAB_FAILED,
            BuildRequest.Status.NETBOX_FAILED,
            BuildRequest.Status.PLAN_FAILED,
            BuildRequest.Status.APPLY_FAILED,
        ):
            build_request = make_build_request(
                vm_name=f"rcd09-d1-{status}", status=status
            )
            self.assertTrue(build_request.is_terminal, status)

    def test_default_ordering_is_newest_first(self):
        older = make_build_request(vm_name="rcd09-d1-older")
        newer = make_build_request(vm_name="rcd09-d1-newer")
        self.assertEqual(list(BuildRequest.objects.all()), [newer, older])

    def test_history_record_created_on_save(self):
        build_request = make_build_request()
        self.assertEqual(build_request.history.count(), 1)
        build_request.status = BuildRequest.Status.COMPLETE
        build_request.save()
        self.assertEqual(build_request.history.count(), 2)

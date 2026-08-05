import frappe
from frappe.tests import IntegrationTestCase

# ponytail: `default_cost_center` (Link -> Cost Center) drags in Cost Center's whole test-record
# dependency chain, which eventually imports erpnext.tests.utils - whose module-level bootstrap
# inserts a "_Test Project" via the now-virtual Project doctype and throws when TaskPilot Settings
# is disabled (the normal state on a fresh/migrated site). Neither test here touches
# default_cost_center, so skip the dependency entirely rather than dragging in an unrelated app's
# test fixtures for a field we never read.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Cost Center"]


class TestTaskPilotSettings(IntegrationTestCase):
	def test_enable_requires_credentials(self):
		settings = frappe.get_doc("TaskPilot Settings")
		settings.enabled = 1
		settings.api_url = ""
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_url_trailing_slash_stripped(self):
		settings = frappe.get_doc("TaskPilot Settings")
		settings.enabled = 0
		settings.api_url = "https://example.test/"
		settings.save()
		self.assertEqual(settings.api_url, "https://example.test")

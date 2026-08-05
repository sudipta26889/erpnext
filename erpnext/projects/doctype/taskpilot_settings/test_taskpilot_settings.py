import frappe
from frappe.tests import IntegrationTestCase


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

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.project_financials import compute_financials


class TestProjectFinancials(IntegrationTestCase):
	def test_empty_project_yields_zeroes(self):
		out = compute_financials("NO-SUCH-PROJECT")
		for key in (
			"total_costing_amount",
			"total_billable_amount",
			"actual_time",
			"total_purchase_cost",
			"total_sales_amount",
			"total_billed_amount",
			"total_consumed_material_cost",
			"gross_margin",
			"per_gross_margin",
		):
			self.assertEqual(out[key] or 0, 0, key)
		self.assertIsNone(out["actual_start_date"])

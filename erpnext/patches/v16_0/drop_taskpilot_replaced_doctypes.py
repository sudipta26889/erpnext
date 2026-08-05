import frappe


def execute():
	for doctype in (
		"Project Template",
		"Project Template Task",
		"Project Update",
		"Project User",
		"Task Depends On",
		"Dependent Task",
	):
		frappe.delete_doc("DocType", doctype, ignore_missing=True, force=True)
	for report in ("Project Summary", "Delayed Tasks Summary", "Project wise Stock Tracking"):
		frappe.delete_doc("Report", report, ignore_missing=True, force=True)
	for chart in ("Completed Projects", "Project Summary"):
		frappe.delete_doc("Dashboard Chart", chart, ignore_missing=True, force=True)
	for card in ("Open Projects", "Non Completed Tasks", "Timesheet Working Hours"):
		frappe.delete_doc("Number Card", card, ignore_missing=True, force=True)
	frappe.delete_doc("Onboarding Step", "View Project Summary", ignore_missing=True, force=True)
	frappe.delete_doc("Dashboard", "Project", ignore_missing=True, force=True)

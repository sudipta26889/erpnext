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

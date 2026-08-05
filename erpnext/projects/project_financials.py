# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import qb
from frappe.query_builder.functions import Max, Min, Sum
from frappe.utils import flt


def _timesheet_totals(project):
	TimesheetDetail = frappe.qb.DocType("Timesheet Detail")
	return (
		frappe.qb.from_(TimesheetDetail)
		.select(
			Sum(TimesheetDetail.costing_amount).as_("costing_amount"),
			Sum(TimesheetDetail.billing_amount).as_("billing_amount"),
			Min(TimesheetDetail.from_time).as_("start_date"),
			Max(TimesheetDetail.to_time).as_("end_date"),
			Sum(TimesheetDetail.hours).as_("time"),
			Sum(TimesheetDetail.base_costing_amount).as_("base_costing_amount"),
			Sum(TimesheetDetail.base_billing_amount).as_("base_billing_amount"),
		)
		.where((TimesheetDetail.project == project) & (TimesheetDetail.docstatus == 1))
	).run(as_dict=True)[0]


def _consumed_material_cost(project):
	parent_doc = frappe.qb.DocType("Stock Entry")
	child_doc = frappe.qb.DocType("Stock Entry Detail")
	lcv_doc = frappe.qb.DocType("Landed Cost Taxes and Charges")

	amount = (
		qb.from_(child_doc)
		.select(Sum(child_doc.amount))
		.where(
			(child_doc.project == project)
			& (child_doc.docstatus == 1)
			& ((child_doc.t_warehouse.isnull()) | (child_doc.t_warehouse == ""))
		)
	).run(as_list=1)

	amount = flt(amount[0][0]) if amount else 0

	additional_costs = (
		qb.from_(parent_doc)
		.join(lcv_doc)
		.on(parent_doc.name == lcv_doc.parent)
		.select(Sum(lcv_doc.base_amount))
		.where(
			(parent_doc.project == project)
			& (parent_doc.docstatus == 1)
			& (parent_doc.purpose == "Manufacture")
		)
	).run(as_list=1)

	additional_cost_amt = flt(additional_costs[0][0]) if additional_costs else 0

	amount += additional_cost_amt
	return amount


def _billed_amount(project):
	si = frappe.qb.DocType("Sales Invoice")
	si_item = frappe.qb.DocType("Sales Invoice Item")
	from_parent = (
		frappe.qb.from_(si)
		.join(si_item)
		.on(si_item.parent == si.name)
		.select(Sum(si_item.base_net_amount))
		.where(
			si_item.project.isnull() & si.project.isnotnull() & (si.project == project) & (si.docstatus == 1)
		)
		.run()
	)
	from_parent = (from_parent and from_parent[0][0]) or 0

	from_child = (
		frappe.qb.from_(si_item)
		.select(Sum(si_item.base_net_amount))
		.where((si_item.project == project) & (si_item.docstatus == 1))
		.run()
	)
	from_child = (from_child and from_child[0][0]) or 0

	return from_parent + from_child


def _purchase_cost(project):
	pitem = qb.DocType("Purchase Invoice Item")
	total_purchase_cost = (
		qb.from_(pitem)
		.select(Sum(pitem.base_net_amount))
		.where((pitem.project == project) & (pitem.docstatus == 1))
		.run(as_list=True)
	)
	return (total_purchase_cost and total_purchase_cost[0][0]) or 0


def _sales_amount(project):
	so = frappe.qb.DocType("Sales Order")
	total_sales_amount = (
		frappe.qb.from_(so)
		.select(Sum(so.base_net_total))
		.where((so.project == project) & (so.docstatus == 1))
		.run()
	)

	return (total_sales_amount and total_sales_amount[0][0]) or 0


def compute_financials(project: str) -> dict:
	ts = _timesheet_totals(project)
	out = {
		"total_costing_amount": flt(ts.base_costing_amount),
		"total_billable_amount": flt(ts.base_billing_amount),
		"actual_time": flt(ts.time),
		"actual_start_date": ts.start_date,
		"actual_end_date": ts.end_date,
		"total_purchase_cost": _purchase_cost(project),
		"total_sales_amount": _sales_amount(project),
		"total_billed_amount": _billed_amount(project),
		"total_consumed_material_cost": _consumed_material_cost(project),
	}
	expense = out["total_costing_amount"] + out["total_purchase_cost"] + out["total_consumed_material_cost"]
	out["gross_margin"] = flt(out["total_billed_amount"]) - expense
	out["per_gross_margin"] = (
		(out["gross_margin"] / out["total_billed_amount"]) * 100 if out["total_billed_amount"] else 0
	)
	return out

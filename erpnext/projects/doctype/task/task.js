// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.projects");

frappe.ui.form.on("Task", {
	setup: function (frm) {
		frm.make_methods = {
			Timesheet: () =>
				frappe.model.open_mapped_doc({
					method: "erpnext.projects.doctype.task.task.make_timesheet",
					frm: frm,
				}),
		};
	},
	onload: function (frm) {
		frm.set_query("project", function () {
			return {
				query: "erpnext.controllers.queries.get_project_name",
			};
		});

		frm.set_query("parent_task", function () {
			let filters = {
				name: ["!=", frm.doc.name],
			};
			if (frm.doc.project) filters["project"] = frm.doc.project;
			return {
				filters: filters,
			};
		});
	},

	validate: function (frm) {
		frm.doc.project && frappe.model.remove_from_locals("Project", frm.doc.project);
	},
});

# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Delete the Page named `ai`, which the AI workspace permanently shadows.

`router.js::convert_to_standard_route` resolves a single-segment desk route
against `frappe.workspaces` *before* falling through to the Page view, so with a
public Workspace named "AI" the route `/desk/ai` could only ever render that
workspace -- the identically-named Page was unreachable, and since the workspace
ships no content blocks the tab rendered blank. The page now ships as `ai-chat`.

Frappe's own orphan sweep (`model/sync.py::remove_orphan_entities`) did not
remove the stale record on either site here, so this does it explicitly.
"""

import frappe


def execute():
	if frappe.db.exists("Page", "ai"):
		frappe.delete_doc("Page", "ai", force=True, ignore_missing=True)

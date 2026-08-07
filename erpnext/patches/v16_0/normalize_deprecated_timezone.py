# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Rewrite deprecated IANA timezone aliases in System Settings.

PostgreSQL's tzdata only knows canonical zone names. Frappe's
`geo/country_info.json` still carries deprecated aliases (India is listed as
`Asia/Calcutta`), and the setup wizard writes whatever it finds there straight
into `System Settings.time_zone`.

The failure mode is total and self-obscuring: every `Database.connect()` issues
`SET TimeZone`, which raises `InvalidParameterValue` on an alias Postgres does
not recognise. Frappe swallows that without a rollback, so the next statement in
`connect()` dies with `InFailedSqlTransaction` — and every request 500s,
including the error page that would have told you why.

This has taken this site down twice. Running on every migrate makes it
self-healing, since anything that re-applies country defaults reintroduces the
alias.
"""

import frappe

# ponytail: only the aliases that have actually bitten us, not all ~120 IANA
# links. Add on sight rather than pre-loading the whole table.
DEPRECATED_ALIASES = {
	"Asia/Calcutta": "Asia/Kolkata",
	"Asia/Katmandu": "Asia/Kathmandu",
	"Asia/Rangoon": "Asia/Yangon",
	"Asia/Saigon": "Asia/Ho_Chi_Minh",
	"Europe/Kiev": "Europe/Kyiv",
	"America/Buenos_Aires": "America/Argentina/Buenos_Aires",
}


def execute():
	current = frappe.db.get_single_value("System Settings", "time_zone")
	canonical = DEPRECATED_ALIASES.get(current)
	if not canonical:
		return

	frappe.db.set_single_value("System Settings", "time_zone", canonical)
	frappe.clear_cache()
	print(f"System Settings.time_zone: {current} -> {canonical} (deprecated alias, breaks PostgreSQL)")

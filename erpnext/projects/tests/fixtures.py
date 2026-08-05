"""Recorded-shape TaskPilot API fixtures (fields per OpenAPI schema, values fake)."""

PROJECT = {
	"id": "11111111-1111-1111-1111-111111111111",
	"name": "Website Revamp",
	"identifier": "WEBSITE",
	"description": "",
	"archived_at": None,
	"external_source": "erpnext",
	"external_id": "PROJ-0001",
	"created_at": "2026-08-01T10:00:00Z",
	"updated_at": "2026-08-05T10:00:00Z",
}

STATES = [
	{"id": "s-backlog", "name": "Backlog", "group": "backlog", "default": False},
	{"id": "s-todo", "name": "Todo", "group": "unstarted", "default": True},
	{"id": "s-progress", "name": "In Progress", "group": "started", "default": False},
	{"id": "s-review", "name": "Pending Review", "group": "started", "default": False},
	{"id": "s-done", "name": "Done", "group": "completed", "default": False},
	{"id": "s-cancel", "name": "Cancelled", "group": "cancelled", "default": False},
]

WORK_ITEM = {
	"id": "22222222-2222-2222-2222-222222222222",
	"name": "Design homepage",
	"description_html": "<p>Hero + nav</p>",
	"priority": "high",
	"start_date": "2026-08-10",
	"target_date": "2026-08-20",
	"sequence_id": 12,
	"parent": None,
	"state": "s-progress",
	"project": "11111111-1111-1111-1111-111111111111",
	"completed_at": None,
	"external_source": "erpnext",
	"external_id": None,
	"created_at": "2026-08-02T09:00:00Z",
	"updated_at": "2026-08-05T09:30:00Z",
}

MEMBERS = [{"id": "u-1", "email": "member@example.com", "display_name": "Test Member"}]

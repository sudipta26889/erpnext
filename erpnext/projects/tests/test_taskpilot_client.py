import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext.projects.taskpilot_client import TaskPilotClient, TaskPilotError, get_client


def _resp(status=200, body=None, headers=None):
	m = MagicMock()
	m.status_code = status
	m.headers = headers or {}
	m.content = json.dumps(body).encode() if body is not None else b""
	m.json.return_value = body
	return m


def _client():
	settings = frappe._dict(
		api_url="https://tp.test", workspace_slug="erpnext", cache_ttl=60, lenient_link_validation=1
	)
	settings.get_password = lambda *a, **kw: "KEY"
	return TaskPilotClient(settings)


class TestTaskPilotClient(IntegrationTestCase):
	def setUp(self):
		frappe.cache.delete_keys("taskpilot|")

	def test_get_client_throws_when_disabled(self):
		frappe.db.set_single_value("TaskPilot Settings", "enabled", 0)
		self.assertRaises(TaskPilotError, get_client)

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_request_builds_workspace_url_and_auth(self, mock_req):
		mock_req.return_value = _resp(body={"ok": 1})
		out = _client().request("GET", "/projects/")
		self.assertEqual(out, {"ok": 1})
		args, kwargs = mock_req.call_args
		self.assertEqual(args, ("GET", "https://tp.test/api/v1/workspaces/erpnext/projects/"))
		self.assertEqual(kwargs["headers"]["X-Api-Key"], "KEY")

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_request_error_surfaces_detail(self, mock_req):
		mock_req.return_value = _resp(status=400, body={"error": "Bad state"})
		self.assertRaises(TaskPilotError, _client().request, "POST", "/projects/", payload={})

	@patch("erpnext.projects.taskpilot_client.time.sleep")
	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_429_backs_off_once_then_succeeds(self, mock_req, mock_sleep):
		mock_req.side_effect = [
			_resp(status=429, headers={"X-RateLimit-Reset": "3"}),
			_resp(body=[]),
		]
		self.assertEqual(_client().request("GET", "/projects/"), [])
		mock_sleep.assert_called_once_with(3)

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_get_cached_hits_network_once(self, mock_req):
		mock_req.return_value = _resp(body={"results": []})
		c = _client()
		c.get_cached("/projects/")
		c.get_cached("/projects/")
		self.assertEqual(mock_req.call_count, 1)

	@patch("erpnext.projects.taskpilot_client.requests.request")
	def test_get_paginated_follows_cursor(self, mock_req):
		mock_req.side_effect = [
			_resp(body={"results": [{"id": 1}], "next_page_results": True, "next_cursor": "20:1:0"}),
			_resp(body={"results": [{"id": 2}], "next_page_results": False}),
		]
		out = _client().get_paginated("/projects/x/work-items/")
		self.assertEqual([r["id"] for r in out], [1, 2])

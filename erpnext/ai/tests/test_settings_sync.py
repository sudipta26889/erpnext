# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import pathlib
import re
import unittest

from frappe.tests import UnitTestCase

# In production the app is copied into the bench without the surrounding repo,
# so prod-docker/ is absent there. Skip rather than fail: this guards the repo,
# and it runs wherever the repo IS present (dev bench, CI, pre-commit).
EXAMPLE = pathlib.Path(__file__).resolve().parents[3] / "prod-docker" / ".env.example"

EXPECTED_KEYS = {
	"PAPERCLIP_URL",
	"PAPERCLIP_COMPANY_ID",
	"PAPERCLIP_AGENT_ID",
	"PAPERCLIP_BOARD_API_KEY",
	"ERPNEXT_MCP_URL",
}


@unittest.skipUnless(EXAMPLE.exists(), "prod-docker/.env.example not present in this checkout")
class TestEnvParity(UnitTestCase):
	def test_env_example_declares_every_paperclip_key(self):
		declared = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", EXAMPLE.read_text(), re.M))
		self.assertTrue(EXPECTED_KEYS <= declared, f"missing from .env.example: {EXPECTED_KEYS - declared}")

	def test_env_example_holds_no_secret_value(self):
		# re.M so ^...$ anchors to the line, not the whole file.
		self.assertRegex(EXAMPLE.read_text(), r"(?m)^PAPERCLIP_BOARD_API_KEY=\s*$")

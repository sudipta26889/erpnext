#!/bin/bash
# Register ERPNext as a Paperclip tool connection and push PAPERCLIP_* into AI Settings.
#
# IMPORTANT: ERPNext must be reachable ONLY through Paperclip's tool gateway. Human
# approval of privileged actions exists solely because governed tool calls traverse
# that gateway, which suspends them into approval requests -- the CEO agent's own
# Claude Code runtime runs with dangerouslySkipPermissions: true, auto-approving
# every tool call it makes. Add this MCP server to any agent's LOCAL Claude Code MCP
# config and every approval control in this design is silently bypassed: submitted
# invoices, cancelled documents and deletions with no human in the loop, and nothing
# will look wrong. That is a defect, not a shortcut. The caps in AI Settings exist
# precisely because this invariant can be broken by configuration outside this repo.
set -euo pipefail
cd "$(dirname "$0")"

set -a
source ./.env
set +a

: "${PAPERCLIP_URL:?blank in .env}"
: "${PAPERCLIP_COMPANY_ID:?blank in .env}"
: "${PAPERCLIP_AGENT_ID:?blank in .env}"
: "${PAPERCLIP_BOARD_API_KEY:?blank in .env}"
: "${ERPNEXT_MCP_URL:?set ERPNEXT_MCP_URL to the LAN URL Paperclip should dial, e.g. http://nuc.lan:4410}"
# Deliberately NOT read from .env: this is the AI service user's ERPNext API
# credential, created by hand in Setup step 2 and consumed here only long enough
# to hand it to Paperclip, which stores it in the connection's transportConfig
# header and replays it on every call thereafter. ERPNext does not need to
# remember it afterwards, so it is not persisted alongside the Paperclip keys --
# export it in the same shell right before running this script:
#   ERPNEXT_API_KEY=... ERPNEXT_API_SECRET=... ./connect-paperclip.sh
: "${ERPNEXT_API_KEY:?ERPNext API key for the AI service user (export inline, not stored in .env)}"
: "${ERPNEXT_API_SECRET:?ERPNext API secret for the AI service user (export inline, not stored in .env)}"

echo "Registering ERPNext as an mcp_remote tool connection…"
curl -sS -X POST \
  -H "Authorization: Bearer $PAPERCLIP_BOARD_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{
        \"name\": \"ERPNext\",
        \"applicationName\": \"ERPNext\",
        \"transport\": \"mcp_remote\",
        \"authKind\": \"api_key\",
        \"connectionKind\": \"managed\",
        \"status\": \"active\",
        \"enabled\": true,
        \"transportConfig\": {
          \"url\": \"${ERPNEXT_MCP_URL}/api/method/erpnext.ai.mcp.handle\",
          \"headers\": {\"Authorization\": \"token ${ERPNEXT_API_KEY}:${ERPNEXT_API_SECRET}\"}
        }
      }" \
  "$PAPERCLIP_URL/api/companies/$PAPERCLIP_COMPANY_ID/tools/connections"
echo

echo "Pushing settings into the site…"
docker compose exec -T \
  -e PC_URL="$PAPERCLIP_URL" \
  -e PC_COMPANY="$PAPERCLIP_COMPANY_ID" \
  -e PC_AGENT="$PAPERCLIP_AGENT_ID" \
  -e PC_KEY="$PAPERCLIP_BOARD_API_KEY" \
  backend bench --site "${SITE_NAME:-erp.localhost}" console <<'PYEOF'
import os

import frappe

doc = frappe.get_doc("AI Settings")
doc.paperclip_url = os.environ["PC_URL"]
doc.paperclip_company_id = os.environ["PC_COMPANY"]
doc.agent_id = os.environ["PC_AGENT"]
doc.board_api_key = os.environ["PC_KEY"]
if not doc.erpnext_company:
    doc.erpnext_company = frappe.db.get_value("Company", {}, "name")
doc.enabled = 1
doc.save()
frappe.db.commit()
print("AI Settings saved for", frappe.local.site, "company", doc.erpnext_company)
PYEOF

echo
echo "Next: create a tool policy in Paperclip requiring approval for"
echo "  submit_document, cancel_document, delete_document, call_method"
echo "and any update_document on a submitted record. Verify with tools/policy/test."

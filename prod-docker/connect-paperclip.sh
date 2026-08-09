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
#
# IMPORTANT: the ERPNEXT_API_KEY/SECRET below belong to an ERPNext user that must also
# hold a role listed in AI Settings -> Allowed Roles (allowed_roles, a JSON list;
# ships as ["System Manager"]). erpnext/ai/mcp.py's _route() calls
# paperclip.assert_ai_user() before every method, including "initialize" -- a service
# user without a listed role gets AI_FORBIDDEN on every call, not just writes. That
# same allowed_roles list also gates the desk AI page for human users, so adding a
# role here to admit this service user grants that role the desk AI tab too.
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
# -f: a 401/4xx/5xx must fail the script, not just print an error body and
# carry on as if it worked.
# -K -: curl reads headers and body from a config file on stdin instead of
# argv. PAPERCLIP_BOARD_API_KEY and the ERPNext key/secret would otherwise
# sit in this process's argv in plaintext for the whole call -- visible to
# any local user via `ps` -- if passed as literal -H/-d arguments instead.
# The response body is deliberately not printed: a reply that echoes
# transportConfig back would print the ERPNext API key to the terminal.
if ! curl -fsS -K - -o /dev/null "$PAPERCLIP_URL/api/companies/$PAPERCLIP_COMPANY_ID/tools/connections" <<CURLCFG
header = "Authorization: Bearer ${PAPERCLIP_BOARD_API_KEY}"
header = "Content-Type: application/json"
data = "{\"name\": \"ERPNext\", \"applicationName\": \"ERPNext\", \"transport\": \"mcp_remote\", \"authKind\": \"api_key\", \"connectionKind\": \"managed\", \"status\": \"active\", \"enabled\": true, \"transportConfig\": {\"url\": \"${ERPNEXT_MCP_URL}/api/method/erpnext.ai.mcp.handle\", \"headers\": {\"Authorization\": \"token ${ERPNEXT_API_KEY}:${ERPNEXT_API_SECRET}\"}}}"
CURLCFG
then
  echo "Registration failed (see curl's exit status/stderr above). Response body suppressed." >&2
  exit 1
fi
echo "Registered."

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
echo "IMPORTANT: the ERPNext user behind ERPNEXT_API_KEY/SECRET must hold a role listed"
echo "in AI Settings -> Allowed Roles (default: [\"System Manager\"]) or every MCP call,"
echo "including initialize, will fail with AI_FORBIDDEN. That same list also gates the"
echo "desk AI tab for human users -- admitting this service user's role admits it there too."
echo
echo "Next: create a tool policy in Paperclip requiring approval for"
echo "  submit_document, cancel_document, delete_document, call_method"
echo "and any update_document on a submitted record. Verify with tools/policy/test."

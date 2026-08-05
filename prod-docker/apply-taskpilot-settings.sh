#!/bin/bash
# Push the TASKPILOT_* values from ./.env into the site's TaskPilot Settings doctype
# (the app reads the Settings single, not env vars — this is the bridge).
# Run from anywhere: ./apply-taskpilot-settings.sh
set -euo pipefail
cd "$(dirname "$0")"

set -a
source ./.env
set +a

: "${TASKPILOT_API_URL:?TASKPILOT_API_URL is blank in .env — fill it first}"
: "${TASKPILOT_WORKSPACE_SLUG:?TASKPILOT_WORKSPACE_SLUG is blank in .env — fill it first}"
: "${TASKPILOT_API_KEY:?TASKPILOT_API_KEY is blank in .env — fill it first}"

# Values travel as env vars into the container (not interpolated into code) so
# special characters in the API key can't break anything.
docker compose exec -T \
	-e TP_URL="$TASKPILOT_API_URL" \
	-e TP_SLUG="$TASKPILOT_WORKSPACE_SLUG" \
	-e TP_KEY="$TASKPILOT_API_KEY" \
	backend bench --site "${SITE_NAME:-erp.localhost}" console <<'PYEOF'
import os
doc = frappe.get_doc("TaskPilot Settings")
doc.api_url = os.environ["TP_URL"]
doc.workspace_slug = os.environ["TP_SLUG"]
doc.api_key = os.environ["TP_KEY"]
doc.enabled = 1
doc.save()
frappe.db.commit()
print("TaskPilot Settings saved and enabled for site:", frappe.local.site)
PYEOF

echo "Applied. Verify in the desk: TaskPilot Settings -> Test Connection."

#!/bin/bash
# Runs inside the tp-bench container (as the container's default user). Ensures MariaDB/Redis
# are up, then execs `bench --site tp.localhost "$@"`. This is the in-container half of
# run-in-bench.sh - do not call directly from the host, use ../run-in-bench.sh instead.
set -euo pipefail

/mnt/projects/erpnext-bench/scripts/ensure-services.sh

cd "$HOME/frappe-bench"
exec bench --site tp.localhost "$@"

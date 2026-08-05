#!/usr/bin/env bash
# The interface every later task uses to run bench/site commands against the TaskPilot virtual
# backend dev site. Runs `bench --site tp.localhost <args>` inside the tp-bench container,
# starting MariaDB/Redis first if they're not already up.
#
# Usage:
#   ./run-in-bench.sh migrate
#   ./run-in-bench.sh run-tests --app erpnext --module erpnext.tests.test_init
#   ./run-in-bench.sh console
#
# For a raw shell in the bench (not a `bench --site ...` subcommand), use:
#   docker compose exec bench bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

exec docker compose exec -T bench /mnt/projects/erpnext-bench/scripts/run-bench-cmd.sh "$@"

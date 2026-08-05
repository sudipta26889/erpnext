#!/bin/bash
# One-time setup, run inside the tp-bench container: symlink apps/erpnext to the host working
# tree (NOT a `bench get-app` copy - that's the whole point, see docker-compose.yml comment),
# editable-install it, create the tp.localhost site, and install the app.
#
# Idempotent-ish: safe to re-run after a partial failure (skips steps already done).
set -euo pipefail

BENCH_DIR="$HOME/frappe-bench"
ERPNEXT_SRC=/mnt/projects/erpnext
DB_PORT=4406

cd "$BENCH_DIR"

# 1. Symlink apps/erpnext -> the bind-mounted host working tree.
if [ -L apps/erpnext ]; then
    echo "apps/erpnext already symlinked -> $(readlink apps/erpnext)"
elif [ -e apps/erpnext ]; then
    echo "apps/erpnext exists and is not a symlink - refusing to touch it" >&2
    exit 1
else
    ln -s "$ERPNEXT_SRC" apps/erpnext
    echo "Symlinked apps/erpnext -> $ERPNEXT_SRC"
fi

# 2. Register it as a bench app (apps.txt) and editable-install into the bench venv.
# NOTE: `echo >> apps.txt` is NOT safe here - bench init's apps.txt has no trailing newline, so a
# bare append would merge onto the last line (observed: "frappeerpnext" as one bogus app name).
# Rewrite the file from its actual lines instead.
if ! grep -qx erpnext sites/apps.txt 2>/dev/null; then
    { grep -v '^$' sites/apps.txt 2>/dev/null; echo erpnext; } > sites/apps.txt.tmp
    mv sites/apps.txt.tmp sites/apps.txt
fi
./env/bin/pip install -q -e apps/erpnext
echo "erpnext editable-installed into bench venv"

# 3. Bring up MariaDB/Redis (same helper run-in-bench.sh uses).
/mnt/projects/erpnext-bench/scripts/ensure-services.sh

# 4. Create the site, if it doesn't already exist.
if [ ! -d sites/tp.localhost ]; then
    bench new-site tp.localhost \
        --mariadb-root-username root \
        --mariadb-root-password root \
        --admin-password admin \
        --db-host 127.0.0.1 \
        --db-port "$DB_PORT" \
        --no-mariadb-socket \
        --set-default
    echo "Site tp.localhost created"
else
    echo "Site tp.localhost already exists"
fi

# 5. Install erpnext on the site (skipped if already installed).
if ! bench --site tp.localhost list-apps | grep -qx erpnext; then
    bench --site tp.localhost install-app erpnext
else
    echo "erpnext already installed on tp.localhost"
fi

echo "Bootstrap complete."

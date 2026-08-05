#!/bin/bash
# Idempotently bring up MariaDB + Redis (cache, queue) inside the tp-bench container, mirroring
# ERPNext CI's .github/helper/start-db.sh / hydrate.sh but on the 4400-4499 port range and with
# a datadir that lives inside the bind-mounted, persistent frappe-bench dir (so it survives
# container recreation). Safe to call on every run-in-bench.sh invocation: does nothing if the
# services are already up.
set -euo pipefail

BENCH_DIR="$HOME/frappe-bench"
DATADIR="$BENCH_DIR/mariadb-data"
SOCK="$DATADIR/mysqld.sock"
DB_PORT=4406

# --- MariaDB ---
fresh=0
if [ ! -d "$DATADIR/mysql" ]; then
    mkdir -p "$DATADIR"
    mariadb-install-db --no-defaults --datadir="$DATADIR" \
        --auth-root-authentication-method=normal --skip-test-db >/dev/null 2>&1
    fresh=1
fi

if ! mariadb-admin --socket="$SOCK" ping --silent 2>/dev/null; then
    mariadbd --no-defaults --datadir="$DATADIR" --socket="$SOCK" --pid-file="$DATADIR/mysqld.pid" \
        --port="$DB_PORT" --bind-address=127.0.0.1 \
        --innodb-flush-log-at-trx-commit=0 --sync-binlog=0 --skip-log-bin \
        > "$BENCH_DIR/mariadb.log" 2>&1 &
    disown
    for _ in $(seq 1 60); do
        mariadb-admin --socket="$SOCK" ping --silent 2>/dev/null && break
        sleep 1
    done
fi
if ! mariadb-admin --socket="$SOCK" ping --silent 2>/dev/null; then
    echo "mariadbd did not come up on $SOCK" >&2
    tail -n 50 "$BENCH_DIR/mariadb.log" >&2 || true
    exit 1
fi

if [ "$fresh" = "1" ]; then
    mariadb --no-defaults --socket="$SOCK" -u root <<'SQL'
ALTER USER 'root'@'localhost' IDENTIFIED BY 'root';
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY 'root';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
FLUSH PRIVILEGES;
SQL
fi

# --- Redis (cache + queue); ports are baked into config/redis_*.conf (4479 / 4480) ---
for conf in redis_cache redis_queue; do
    cfg="$BENCH_DIR/config/$conf.conf"
    [ -f "$cfg" ] || continue
    port=$(awk '/^port /{print $2}' "$cfg")
    if ! redis-cli -p "$port" ping >/dev/null 2>&1; then
        redis-server "$cfg" --daemonize yes
        for _ in $(seq 1 30); do
            redis-cli -p "$port" ping >/dev/null 2>&1 && break
            sleep 1
        done
    fi
    redis-cli -p "$port" ping >/dev/null 2>&1 || { echo "redis ($conf) did not come up on port $port" >&2; exit 1; }
done

echo "Services up: mariadb (port $DB_PORT), redis_cache (4479), redis_queue (4480)."

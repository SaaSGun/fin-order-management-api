#!/usr/bin/env bash
# The production load-test run behind the README's production numbers.
#
# Run from Google Cloud Shell at the repo root (it needs gcloud credentials
# for the project and a network path to Cloud SQL through the Auth Proxy):
#   bash loadtest/run_production.sh
#
# 1. cleans up leftovers of earlier runs, 2. provisions per-run API keys,
# 3. runs the race probe, 4. runs Locust (paced, then near-zero think time
# at rising concurrency), 5. checks the concentration limit held under load,
# cancels the run's open orders and revokes its keys, 6. prints a summary.
# Raw results land in loadtest/results/ (gitignored).
set -uo pipefail

PROJECT=${PROJECT:-fin-oms-api}
REGION=${REGION:-europe-west4}
SERVICE=${SERVICE:-fin-oms-api}
HOST=${HOST:-https://fin-oms-api-miatwtkcba-ez.a.run.app}
FAST_USERS=${FAST_USERS:-"100 250 500"}
RUN_ID=$(date -u +%Y%m%dT%H%MZ)
OUT=loadtest/results/prod-$RUN_ID
KEYS=loadtest/.keys-prod.json
mkdir -p "$OUT"

step() { echo; echo "=== $(date -u +%H:%M:%SZ) $* ==="; }

step "setup"
python3 -m venv /tmp/oms-venv && . /tmp/oms-venv/bin/activate
pip install -q -r requirements-dev.txt
if [ ! -x /tmp/cloud-sql-proxy ]; then
  curl -sSLo /tmp/cloud-sql-proxy \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.linux.amd64"
  chmod +x /tmp/cloud-sql-proxy
fi
/tmp/cloud-sql-proxy --port 5433 "$PROJECT:$REGION:$SERVICE-pg" > /tmp/cloud-sql-proxy.log 2>&1 &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null' EXIT
for _ in $(seq 1 30); do (echo > /dev/tcp/127.0.0.1/5433) 2>/dev/null && break; sleep 1; done
DB_PASSWORD=$(gcloud secrets versions access latest --secret="$SERVICE-db-password" --project "$PROJECT")
export DATABASE_URL="postgresql+asyncpg://fin_oms:${DB_PASSWORD}@127.0.0.1:5433/fin_oms"
export PYTHONPATH=.
python - <<'EOF'
import asyncio, os, asyncpg
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    print("max_connections:", await c.fetchval("SHOW max_connections"),
          "| superuser_reserved:", await c.fetchval("SHOW superuser_reserved_connections"),
          "| open now:", await c.fetchval("SELECT count(*) FROM pg_stat_activity"))
    await c.close()
asyncio.run(main())
EOF

step "1. cleanup of earlier runs"
python -m scripts.cancel_load_test_orders --revoke-keys | tee "$OUT/cleanup-before.txt"

step "2. provision keys (one per simulated PM)"
python -m scripts.provision_load_test_keys --count 500 --prefix "loadtest-prod-$RUN_ID" --out "$KEYS"
step "waiting 310s: warm instances cache valid API keys for 5 min"
sleep 310

step "3. race probe, 10 simultaneous PMs"
python -m loadtest.concurrency_probe --host "$HOST" --keys-file "$KEYS" --concurrency 10 | tee "$OUT/probe.txt"

export LOADTEST_KEYS_FILE=$KEYS
step "4a. Locust: 100 PMs, 1-4 s think time, 2 min"
locust -f loadtest/locustfile.py --host "$HOST" --headless -u 100 -r 10 -t 2m --only-summary \
  --csv "$OUT/paced-100" > "$OUT/paced-100.log" 2>&1
for users in $FAST_USERS; do
  sleep 30
  step "4b. Locust: $users clients, near-zero think time, 2 min"
  LOADTEST_FAST=1 locust -f loadtest/locustfile.py --host "$HOST" --headless -u "$users" -r 50 -t 2m --only-summary \
    --csv "$OUT/fast-$users" > "$OUT/fast-$users.log" 2>&1
done

step "5. did the concentration limit hold under load? then cancel + revoke"
python -m scripts.cancel_load_test_orders --revoke-keys | tee "$OUT/cleanup-after.txt"

step "6. summary ($OUT)"
python - "$OUT" <<'EOF'
import csv, collections, glob, os, sys
out = sys.argv[1]
for stats in sorted(glob.glob(f"{out}/*_stats.csv")):
    run = os.path.basename(stats).removesuffix("_stats.csv")
    for r in csv.DictReader(open(stats)):
        if (r["Type"], r["Name"]) in (("", "Aggregated"), ("POST", "/orders")):
            print(f"{run:9} {r['Name']:10} n={r['Request Count']:>6} fail={r['Failure Count']:>5} "
                  f"rps={float(r['Requests/s']):6.1f} p50={r['50%']:>5} p95={r['95%']:>5} "
                  f"p99={r['99%']:>5} max={float(r['Max Response Time']):6.0f}")
    errors = collections.Counter()
    failures = f"{out}/{run}_failures.csv"
    for r in csv.DictReader(open(failures)) if os.path.exists(failures) else []:
        errors[r["Error"][:110]] += int(r["Occurrences"])
    for error, n in errors.most_common(5):
        print(f"{'':9} {n}x {error}")
    log = open(f"{out}/{run}.log").read()
    if "CPU usage" in log:
        print(f"{'':9} WARNING: Locust client CPU-bound in this run - numbers understate the server")
EOF

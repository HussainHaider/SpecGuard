#!/usr/bin/env bash
# Prove the quickstart works from a clone, not from this working tree.
#
# The failure this catches is a file that exists on the author's disk and is not in the
# repository — an uncommitted fixture, a .env nobody else has, a corpus someone fetched
# once and forgot. Running compose here proves nothing about that; running it against a
# fresh `git clone` is the only version of the test worth having.
#
#   ./scripts/clean-clone-test.sh [target-dir]
#
# Uses its own compose project name and its own ports, so it cannot collide with a stack
# already running from the working tree.
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$(mktemp -d)/specguard}"
PROJECT="specguard-cleanclone"

# Deliberately different from the defaults, so a service that is somehow reaching the
# developer's already-running stack fails here instead of appearing to work.
export POSTGRES_PORT=55432 QDRANT_HTTP_PORT=56333 QDRANT_GRPC_PORT=56334 \
       REDIS_PORT=56379 API_PORT=58000 WEB_PORT=55173 N8N_PORT=55678

cleanup() {
  echo "--- tearing down"
  (cd "$TARGET" && docker compose -p "$PROJECT" --profile app down -v --remove-orphans) || true
}
trap cleanup EXIT

echo "--- cloning $SOURCE -> $TARGET"
git clone --quiet "$SOURCE" "$TARGET"
cd "$TARGET"

echo "--- .env from .env.example, untouched"
cp .env.example .env

echo "--- command 1 of 2: docker compose --profile app up"
docker compose -p "$PROJECT" --profile app up -d --build

echo "--- waiting for the API"
for _ in $(seq 1 90); do
  if curl -fsS -m 5 "http://localhost:${API_PORT}/healthz" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS -m 10 "http://localhost:${API_PORT}/healthz"
echo

echo "--- command 2 of 2: seed the corpus"
docker compose -p "$PROJECT" exec -T api python -m specguard.corpus.seed

echo "--- submitting a sample specification"
JOB=$(curl -fsS -m 60 -X POST "http://localhost:${API_PORT}/checks?language=en" \
  -F "file=@fixtures/specs/generated/SPEC-021-pasta-sauce.pdf" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["job_id"])')
echo "    job $JOB"

for _ in $(seq 1 120); do
  STATUS=$(curl -fsS -m 10 "http://localhost:${API_PORT}/checks/${JOB}" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  [ "$STATUS" = "succeeded" ] && break
  [ "$STATUS" = "failed" ] && { echo "check failed"; exit 1; }
  sleep 3
done

curl -fsS -m 10 "http://localhost:${API_PORT}/checks/${JOB}" > /tmp/ccr.json
python3 - /tmp/ccr.json <<'PYCHECK'
import json, sys

report = json.load(open(sys.argv[1]))["report"]
counts = {k: v for k, v in report["counts"].items() if v}
print("    verdict", report["overall_verdict"], counts, "in", report["duration_ms"], "ms")
assert report["results"], "no rule results"
for result in report["results"]:
    if result["verdict"] != "NEEDS_REVIEW":
        assert result["citations"], f"{result['rule_id']} decided without a citation"
print("    rules:", len(report["results"]), "| citations resolve: yes")
PYCHECK

echo "--- checking the UI is served"
curl -fsS -m 10 -o /dev/null -w "    web %{http_code}\n" "http://localhost:${WEB_PORT}/"

echo
echo "PASS: two commands from a clean clone."

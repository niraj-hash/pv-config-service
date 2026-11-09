#!/usr/bin/env bash
# Registers the local dev servers that aren't Spring Boot (web apps, the demo browser worker) in
# the local Consul (docker compose), so pg-api-gateway reaches them as lb://<name>, the same way as
# in production (where Consul's Kubernetes catalog sync registers them; see deploy/consul-values.yaml).
#
#   CONSUL_HTTP_TOKEN=$(cat secrets/management.token) scripts/register-local.sh [--remove]
#
# Each service is registered with address 127.0.0.1 (where the gateway, on this machine, connects)
# and a TCP health check that Consul, inside Docker, runs against host.docker.internal.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
ADDR=${CONSUL_HTTP_ADDR:-http://127.0.0.1:8500}
: "${CONSUL_HTTP_TOKEN:?a token that may register services (local: secrets/management.token)}"
CHECK_HOST=${CHECK_HOST:-host.docker.internal}
REMOVE=false
[ "${1:-}" = "--remove" ] && REMOVE=true

python3 - "$ROOT/consul/local-services.json" "$CHECK_HOST" <<'PY' | while IFS=$'\t' read -r name body; do
import json, sys
spec, check_host = json.load(open(sys.argv[1])), sys.argv[2]
for s in spec["services"]:
    body = {"ID": s["name"] + "-local", "Name": s["name"], "Address": "127.0.0.1", "Port": s["port"],
            "Tags": ["local"], "Check": {"Name": "port open", "TCP": f"{check_host}:{s['port']}",
            "Interval": "10s", "Timeout": "2s", "DeregisterCriticalServiceAfter": "30m"}}
    print(s["name"] + "\t" + json.dumps(body))
PY
  if [ "$REMOVE" = true ]; then
    curl --fail -sS -X PUT -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" "$ADDR/v1/agent/service/deregister/$name-local" >/dev/null && echo "removed $name"
  else
    curl --fail -sS -X PUT -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" --data-binary "$body" "$ADDR/v1/agent/service/register" >/dev/null && echo "registered $name"
  fi
done

#!/usr/bin/env bash
# Creates (or updates) Consul ACL policies, and tokens, for pv-config-service:
#   - one policy per service in consul/services.txt (consul/policies/service.hcl.tpl): read its
#     own config and the shared defaults, register itself, discover the others;
#   - the config-loader policy (write config/ only), for scripts/sync.sh (the consul-config job).
#
#   CONSUL_HTTP_ADDR=http://127.0.0.1:8500 CONSUL_HTTP_TOKEN=<management token> scripts/bootstrap-acl.sh [--tokens]
#   scripts/bootstrap-acl.sh --init      # local only: bootstraps a fresh Consul's ACLs first
#
# Tokens are written to secrets/<name>.token (git-ignored, mode 600). Hand each service its own
# token as CONSUL_TOKEN (a Kubernetes Secret / Key Vault entry), never through Consul KV.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
ADDR=${CONSUL_HTTP_ADDR:-http://127.0.0.1:8500}
SECRETS="$ROOT/secrets"
TOKENS=false
INIT=false
for arg in "$@"; do
  case "$arg" in
    --tokens) TOKENS=true ;;
    --init) INIT=true; TOKENS=true ;;
    *) echo "unknown option $arg" >&2; exit 2 ;;
  esac
done
mkdir -p "$SECRETS" && chmod 700 "$SECRETS"
umask 077

if [ "$INIT" = true ]; then
  if [ ! -s "$SECRETS/management.token" ]; then
    curl --fail -sS -X PUT "$ADDR/v1/acl/bootstrap" | python3 -c 'import json,sys; print(json.load(sys.stdin)["SecretID"])' > "$SECRETS/management.token"
    echo "bootstrapped ACLs; management token in secrets/management.token"
  fi
  CONSUL_HTTP_TOKEN=$(cat "$SECRETS/management.token")
fi
: "${CONSUL_HTTP_TOKEN:?a management token is required (or --init on a fresh local Consul)}"

api() { # method path [body]
  if [ $# -ge 3 ]; then
    curl --fail -sS -X "$1" -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" --data-binary "$3" "$ADDR/v1/$2"
  else
    curl --fail -sS -X "$1" -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" "$ADDR/v1/$2"
  fi
}

policy() { # name rules
  local name=$1 rules=$2 body id
  body=$(python3 -c 'import json,sys; print(json.dumps({"Name": sys.argv[1], "Description": "pv-config-service", "Rules": sys.argv[2]}))' "$name" "$rules")
  id=$(api GET "acl/policy/name/$name" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["ID"])' 2>/dev/null || true)
  if [ -n "$id" ]; then api PUT "acl/policy/$id" "$body" >/dev/null; echo "updated policy $name"
  else api PUT "acl/policy" "$body" >/dev/null; echo "created policy $name"; fi
}

token() { # name
  local name=$1
  [ "$TOKENS" = true ] || return 0
  if [ -s "$SECRETS/$name.token" ]; then echo "token for $name exists (secrets/$name.token)"; return 0; fi
  api PUT "acl/token" "{\"Description\":\"$name\",\"Policies\":[{\"Name\":\"$name\"}]}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["SecretID"])' > "$SECRETS/$name.token"
  echo "created token for $name (secrets/$name.token)"
}

policy config-loader "$(cat "$ROOT/consul/policies/config-loader.hcl")"
token config-loader

grep -vE '^\s*(#|$)' "$ROOT/consul/services.txt" | while read -r service; do
  policy "$service" "$(sed "s/__SERVICE__/$service/g" "$ROOT/consul/policies/service.hcl.tpl")"
  token "$service"
done

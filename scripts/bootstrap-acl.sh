#!/usr/bin/env bash
# Creates (or updates) Consul ACL policies, and tokens, for pv-config-service:
#   - one policy per service in consul/services.txt (consul/policies/service.hcl.tpl): read its
#     own config and the shared defaults, register itself, discover the others;
#   - the config-loader policy (write config/ only), for scripts/sync.sh (the consul-config job).
#
#   CONSUL_HTTP_ADDR=http://127.0.0.1:8500 CONSUL_HTTP_TOKEN=<management token> scripts/bootstrap-acl.sh [--tokens]
#   scripts/bootstrap-acl.sh --init      # local only: bootstraps a fresh Consul's ACLs first
#
# Tokens are written to secrets/<name>.token (git-ignored, mode 600), or to $CONSUL_SECRETS_DIR when
# set. A token whose file exists is kept, not created again, so against a cluster's Consul point
# CONSUL_SECRETS_DIR at an empty directory: otherwise the local Consul's tokens are kept and the
# cluster rejects them. A missing file is filled from the token Consul already has for that name
# (same description and policy), so running it again after losing the files makes no second token. Hand each service its own token as CONSUL_TOKEN (a Kubernetes Secret /
# Key Vault entry), never through Consul KV.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
ADDR=${CONSUL_HTTP_ADDR:-http://127.0.0.1:8500}
SECRETS=${CONSUL_SECRETS_DIR:-$ROOT/secrets}
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
  local name=$1 accessor
  [ "$TOKENS" = true ] || return 0
  if [ -s "$SECRETS/$name.token" ]; then echo "token for $name exists ($SECRETS/$name.token)"; return 0; fi
  # A token made earlier for this name whose file is gone: read it back instead of making another.
  accessor=$(api GET "acl/tokens" | python3 -c '
import json, sys
mine = [t for t in json.load(sys.stdin) if t.get("Description") == sys.argv[1]
        and [p.get("Name") for p in t.get("Policies") or []] == [sys.argv[1]]]
mine.sort(key=lambda t: t.get("CreateTime", ""))
print(mine[-1]["AccessorID"] if mine else "")' "$name")
  if [ -n "$accessor" ]; then
    api GET "acl/token/$accessor" | python3 -c 'import json,sys; print(json.load(sys.stdin)["SecretID"])' > "$SECRETS/$name.token"
    echo "read back the existing token for $name ($SECRETS/$name.token)"
    return 0
  fi
  api PUT "acl/token" "{\"Description\":\"$name\",\"Policies\":[{\"Name\":\"$name\"}]}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["SecretID"])' > "$SECRETS/$name.token"
  echo "created token for $name ($SECRETS/$name.token)"
}

policy config-loader "$(cat "$ROOT/consul/policies/config-loader.hcl")"
token config-loader

grep -vE '^\s*(#|$)' "$ROOT/consul/services.txt" | while read -r service; do
  policy "$service" "$(sed "s/__SERVICE__/$service/g" "$ROOT/consul/policies/service.hcl.tpl")"
  token "$service"
done

#!/usr/bin/env bash
# Publishes config/ to Consul KV, after validating it.
#
# Each directory config/<context>/ becomes the key config/<context>/data, which Spring Cloud
# Consul reads as YAML: data.yml first, then conf.d/*.yml in name order, each as its own YAML
# document (later documents add to earlier ones). <context> is a service name, "application"
# (shared by all), or "<service>,<profile>" for a profile's overrides.
#
#   CONSUL_HTTP_ADDR=http://127.0.0.1:8500 CONSUL_HTTP_TOKEN=<config-loader token> scripts/sync.sh [--dry-run] [--prune]
#
#   --dry-run  print what would be written, change nothing
#   --prune    also delete config/<context>/data keys whose directory no longer exists
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
ADDR=${CONSUL_HTTP_ADDR:-http://127.0.0.1:8500}
DRY_RUN=false
PRUNE=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --prune) PRUNE=true ;;
    *) echo "unknown option $arg" >&2; exit 2 ;;
  esac
done
if [ "$DRY_RUN" = false ] && [ -z "${CONSUL_HTTP_TOKEN:-}" ]; then
  echo "CONSUL_HTTP_TOKEN must be the config-loader token (scripts/bootstrap-acl.sh creates it)" >&2
  exit 2
fi

python3 "$ROOT/scripts/validate.py"

consul() { # method path [curl args...]
  local method=$1 path=$2; shift 2
  curl --fail --silent --show-error -X "$method" -H "X-Consul-Token: ${CONSUL_HTTP_TOKEN:-}" "$@" "$ADDR/v1/kv/$path"
}

published=()
for dir in "$ROOT"/config/*/; do
  context=$(basename "$dir")
  files=()
  [ -f "$dir/data.yml" ] && files+=("$dir/data.yml")
  if [ -d "$dir/conf.d" ]; then
    while IFS= read -r f; do files+=("$f"); done < <(find "$dir/conf.d" -maxdepth 1 -name '*.yml' | sort)
  fi
  [ ${#files[@]} -eq 0 ] && continue
  body=$(for f in "${files[@]}"; do printf -- '---\n# %s\n' "${f#"$ROOT"/}"; cat "$f"; printf '\n'; done)
  published+=("config/$context/data")
  if [ "$DRY_RUN" = true ]; then
    echo "would write config/$context/data (${#files[@]} files, ${#body} bytes)"
  else
    # Through stdin: a large context (pg-api-gateway's) exceeds the 128 KB limit on one argument.
    printf '%s' "$body" | consul PUT "config/$context/data" --data-binary @- >/dev/null
    echo "wrote config/$context/data (${#files[@]} files)"
  fi
done

if [ "$PRUNE" = true ]; then
  existing=$(consul GET "config/?keys" 2>/dev/null | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)))' || true)
  for key in $existing; do
    case "$key" in */data) ;; *) continue ;; esac
    if ! printf '%s\n' "${published[@]}" | grep -qxF "$key"; then
      if [ "$DRY_RUN" = true ]; then echo "would delete $key"; else consul DELETE "$key" >/dev/null; echo "deleted $key"; fi
    fi
  done
fi

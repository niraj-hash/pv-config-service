#!/usr/bin/env bash
# Lints the runvance-service library and renders every chart built on it, offline: no registry,
# no cluster. For each chart: its dependency (a file:// one: helm dependency build; the published
# oci:// one: this checkout's library packaged into charts/, which is git-ignored, so the check
# covers the library as it is here), then helm lint and helm template with each values-<env>.yaml,
# then kubeconform on the output when it is installed (CRDs such as ExternalSecret and Certificate
# are skipped without their schemas). Image tags come from release files, and charts without an
# appVersion refuse to render without one, so the check sets a placeholder tag.
#
#   deploy/charts/check.sh                 # the platform charts in the sibling checkouts
#   deploy/charts/check.sh <chart dir>...  # a product's chart
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WORKSPACE=$(cd "$HERE/../../.." && pwd)
LIB="$HERE/runvance-service"
# A release file sets image.tag (one workload) or common.image.tag (several); set both.
TAGS=(--set image.tag=check --set common.image.tag=check)

charts=("$@")
if [ ${#charts[@]} -eq 0 ]; then
  charts=(
    "$WORKSPACE/pg-api-gateway/deploy/helm/pg-api-gateway"
    "$WORKSPACE/pv-auth-service/deploy/helm/pv-auth-service"
    "$WORKSPACE/notification-service/deploy/helm/notification-service"
  )
fi

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

helm lint "$LIB"

for chart in "${charts[@]}"; do
  if [ ! -d "$chart" ]; then
    echo "skip $chart (not checked out)"
    continue
  fi
  name=$(basename "$chart")
  if grep -qE '^[[:space:]]*repository:[[:space:]]*"?oci://' "$chart/Chart.yaml"; then
    mkdir -p "$chart/charts"
    helm package "$LIB" -d "$chart/charts" >/dev/null
  else
    helm dependency build "$chart" >/dev/null
  fi
  shopt -s nullglob
  envs=("$chart"/values-*.yaml)
  shopt -u nullglob
  [ ${#envs[@]} -gt 0 ] || { echo "$chart has no values-<env>.yaml" >&2; exit 1; }
  for values in "${envs[@]}"; do
    env=$(basename "$values" .yaml)
    env=${env#values-}
    helm lint "$chart" -f "$values" "${TAGS[@]}" --strict >/dev/null || { helm lint "$chart" -f "$values" "${TAGS[@]}" --strict; exit 1; }
    helm template "$name" "$chart" -f "$values" "${TAGS[@]}" > "$out/$name-$env.yaml"
    kinds=$(grep -E '^kind: ' "$out/$name-$env.yaml" | sort | uniq -c | awk '{printf "%s %s, ", $1, $3}')
    echo "ok  $name ($env): ${kinds%, }"
    if command -v kubeconform >/dev/null 2>&1; then
      kubeconform -strict -summary -ignore-missing-schemas "$out/$name-$env.yaml"
    fi
  done
done

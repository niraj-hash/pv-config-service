#!/usr/bin/env bash
# Releases one Runvance service to one environment: optionally builds its images with az acr build
# (tag = the short git SHA), then writes deploy/releases/<env>/<service>.yaml (the image tags and the
# chart commit), checks every release file, and prints the git and argocd commands to finish. Nothing
# is deployed here: Argo CD deploys when someone presses Sync (docs/deploy-with-argocd.md).
#
#   scripts/release.sh <service> <env> --build [--force]          build HEAD of the service's repository
#   scripts/release.sh <service> <env> --tag <tag>                 images built earlier
#   scripts/release.sh <service> <env> --tag <image>=<tag> ...     one image at a time
#   scripts/release.sh <service> prod --from staging --import      promote what staging names
#   options: --chart-revision <ref>  --repo-dir <dir>  --skip-image-check  --dry-run
#
# Services, images, Dockerfiles and registries: deploy/services.yaml. Needs python3 with PyYAML, git,
# and for --build / --import / the image check the Azure CLI, logged in (az login; az acr login is not needed).
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/releases.py" release "$@"

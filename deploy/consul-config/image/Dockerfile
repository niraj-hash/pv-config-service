# pv-config-sync: the tools the consul-config job runs (scripts/sync.sh and scripts/validate.py come from
# the ConfigMap at the released commit, not from this image). Rebuild only to change the tools:
#   scripts/release.sh consul-config <env> --build
FROM python:3.12-alpine3.22
RUN apk add --no-cache bash curl \
 && pip install --no-cache-dir pyyaml==6.0.2
# The platform's unprivileged user; the job's root file system is read-only (/work and /tmp are not).
USER 10001
WORKDIR /work

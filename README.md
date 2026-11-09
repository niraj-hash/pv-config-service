# pv-config-service

Configuration and service discovery for every PayVizio service, on **Consul**. This repository
is the configuration as code; Consul serves it (KV) and keeps the service catalog that
pg-api-gateway routes to (`lb://<service>`). Part of the platform described in
[pg-api-gateway/docs/architecture.md](https://github.com/payvizio-pg/pg-api-gateway/blob/main/docs/architecture.md).

```
config/
  application/data.yml              → config/application/data          every service
  pg-api-gateway/data.yml           → config/pg-api-gateway/data       gateway settings
  pg-api-gateway/conf.d/<p>.yml       (joined into the same key)       a product's hosts, routes, RBAC rules
  pv-auth-service/data.yml          → config/pv-auth-service/data      auth settings
  pv-auth-service/conf.d/<p>.yml      (joined into the same key)       a product's permissions and roles
  <service>,<profile>/…             → config/<service>,<profile>/data  a Spring profile's overrides (e.g. local)
consul/
  server.hcl                        Consul server (ACLs on, default deny)
  policies/                         least-privilege policy per service, and the config loader's
  services.txt                      services that get a policy and a token
  local-services.json               local dev servers to register (name, port)
scripts/
  validate.py                       YAML, no secrets, rules ↔ catalog agree, rate limits, breakers, Resilience4j
  sync.sh                           validate, then publish config/ to Consul KV
  bootstrap-acl.sh                  create/update policies and tokens
  register-local.sh                 register local dev servers that aren't Spring (web apps, worker)
  release.sh                        build a service's images (az acr build) and write its release file
  releases.py                       the release files' checks (`python3 scripts/releases.py check`)
deploy/
  consul-values.yaml                Consul on Kubernetes (Helm): servers, ACLs, TLS, catalog sync
  charts/runvance-service/          the Helm library chart every service's chart builds on
  charts/check.sh                   lint and render the library and the charts built on it, offline
  services.yaml                     every service's repository and images (what release.sh builds)
  releases/<env>/<service>.yaml     what Argo CD deploys: the chart's commit and the image tags
  argocd/                           the AppProject and the ApplicationSets (one Application per service and env)
  consul-config/                    the chart that publishes config/ to Consul (Application consul-config-<env>)
docs/
  deploy-with-argocd.md             deploy, roll back and promote any service by hand from Argo CD
  platform-contract.md              what products never build themselves
  rate-limits.md                    every product's former rate limiter and its gateway rule
  runtime.md                        AKS conventions: namespaces, labels, images, secrets, adding a service
```

Services read it with Spring Cloud Consul Config (`format: YAML`, `prefixes: config`,
`default-context: application`, `profile-separator: ","`, `data-key: data`); see
[product-integration.md §7](https://github.com/payvizio-pg/pg-api-gateway/blob/main/docs/product-integration.md).

## Rules

- **No secrets here.** Passwords, keys, tokens and client secrets come from the environment
  (Kubernetes Secrets / Key Vault). `validate.py` fails on a secret-looking key holding anything
  but an `${ENV_VAR}` placeholder.
- **Least privilege.** Each service's Consul token reads `config/application*` and its own
  `config/<service>*` keys, registers itself and discovers others. Nothing else. The config-loader
  token (the consul-config job's) writes `config/` only.
- **One file per product** in each `conf.d/`. The gateway's rules may only name permissions and
  roles that the product's catalog declares (`validate.py` checks).
- **Keep `application` small**: what it sets overrides each service's packaged defaults.
- A change reaches pv-auth-service at its next start (catalog sync) and the gateway at its next
  rolling restart.

## Hosts

Every product is served only from a `runvance.com` subdomain (owner's decision, 2026-11-09): the
gateway's `hosts` in `config/pg-api-gateway/conf.d/*.yml` and pv-auth-service's catalog `origins`
list nothing else, apart from `*.localhost` for local runs. The move off the old domains, and
everything outside these repositories it needs, is in
[docs/runvance-domain-cutover.md](docs/runvance-domain-cutover.md).

## Pricing

Each product's plans and prices are set in exactly one place, `config/<service>/conf.d/pricing.yml`
(`pv.pricing`: currency, tax, what a price is for, and per plan its monthly/yearly price, trial,
limits and features). Only that service's own Consul token reads it, so the website and anonymous
visitors never see it; sales shares prices on request. The product writes these plans into its own
database at start-up and refuses price edits in its admin screens, so nothing else can hold a
different price. Change a price: edit the file, `scripts/validate.py`, `scripts/sync.sh`, restart the
service. The validator refuses pricing anywhere other services can read (`application`, the
gateway, pv-auth-service) and malformed plans. The gateway asks for sign-in on the products' price
endpoints, so signed-in customers still see their plans in the app.

## Service discovery

Every service finds the others through Consul's catalog, never by fixed address:

- **Spring services register themselves** (`spring-cloud-starter-consul-discovery`, with their
  readiness check) under `spring.application.name`; pg-api-gateway routes to `lb://<name>`.
- **Services that aren't Spring** (web apps, the demo browser worker): on Kubernetes the Consul Helm
  chart's catalog sync registers each Service annotated `consul.hashicorp.com/service-sync: "true"`
  (`deploy/consul-values.yaml`); locally `scripts/register-local.sh` registers the dev servers in
  `consul/local-services.json`.
- **Only healthy instances get traffic** (`query-passing: true` in `config/application`); an
  instance critical for 2 minutes is removed. Local runs relax this (`config/application,local`),
  because Consul in Docker Desktop can't always reach health checks on the host.
- Clients that aren't Spring use Consul DNS: `<name>.service.consul`.

## Local

```bash
docker compose up -d                                     # Consul on 127.0.0.1:8500
scripts/bootstrap-acl.sh --init                          # ACLs, policies, tokens → secrets/ (git-ignored)
CONSUL_HTTP_TOKEN=$(cat secrets/config-loader.token) scripts/sync.sh
CONSUL_HTTP_TOKEN=$(cat secrets/management.token) scripts/register-local.sh   # web apps, worker
```

Then start a service with `CONFIG_IMPORT=consul: CONSUL_TOKEN=$(cat secrets/<service>.token)
SPRING_PROFILES_ACTIVE=local SPRING_CLOUD_CONSUL_DISCOVERY_IP_ADDRESS=127.0.0.1`. It registers at
127.0.0.1, where the other services on this machine reach it; Consul inside Docker can't run its
health check there, which is why the `local` profile lets discovery use instances that aren't
passing.

## Production

- Three Consul servers and a client agent per node (the official `hashicorp/consul` Helm chart,
  `deploy/consul-values.yaml`): ACLs on with default deny, TLS and gossip encryption on, catalog
  sync for annotated Kubernetes Services, Consul DNS, snapshots backed up.
- Publishing is a deploy like any other, by hand from Argo CD (no CI; owner's decision,
  2026-11-09): the Application `consul-config-<env>` renders `deploy/consul-config` at the commit
  its release file pins (`scripts/release.sh consul-config <env>` pins HEAD); Sync applies a
  ConfigMap holding `config/` and the two scripts at that commit (Argo CD's diff shows the
  configuration change), then a PostSync job runs `scripts/sync.sh --prune` (which runs
  `scripts/validate.py` first) with the config-loader token from Key Vault
  (`pv-config-service-config-loader-token`). Rollback: History -> Rollback re-publishes the earlier
  commit, `--prune` removing what the later one added. Then restart pg-api-gateway (and
  pv-auth-service when its catalog changed) with Argo CD's Restart action. Runbook:
  [docs/deploy-with-argocd.md](docs/deploy-with-argocd.md).
- `scripts/sync.sh` stays the way to publish to a local Consul (above) and the job's own script.
- Before pushing: `python3 scripts/validate.py` and `python3 scripts/releases.py check` (nothing
  runs them for you).
- Each service's token is created once by `scripts/bootstrap-acl.sh --tokens` (with a management
  token) and stored as that service's `CONSUL_TOKEN` secret.

Verified locally on 2026-11-08 against Consul 2.0.4: bootstrap, sync, the gateway starting from
Consul with its own token and serving routes merged from several product files, and the token
refused on another service's keys and on writes.

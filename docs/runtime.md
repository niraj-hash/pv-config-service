# Runtime: Runvance services on AKS

How every Runvance service runs (platform contract, "Runtime"): on AKS, configured and discovered
through Consul, each with a Helm chart built on the one library chart
[`deploy/charts/runvance-service`](../deploy/charts/runvance-service/README.md). No Azure Container
Apps, no App Service. Change the conventions here first.

## Clusters

| Environment | Cluster | Registry | Key Vault (ClusterSecretStore `azure-keyvault`) |
|---|---|---|---|
| `staging` | `aks-interviewai-staging` (centralindia) | `acrinterviewaistaging.azurecr.io` | `kv-iai-staging` |
| `prod` | not provisioned | to be created | to be created |

Each cluster runs: Consul (`consul` namespace, `deploy/consul-values.yaml`), External Secrets
Operator with the `azure-keyvault` ClusterSecretStore, cert-manager, the AKS app-routing add-on
(managed ingress-nginx, class `webapprouting.kubernetes.azure.com`, namespace `app-routing-system`),
Prometheus (`monitoring`), and a CNI that enforces NetworkPolicy (Azure CNI powered by Cilium).

Both environments read the same configuration (this repository synced into each cluster's Consul),
so both answer the same `runvance.com` hosts; only one can own their DNS at a time. Today that is
the staging cluster. A separate set of staging hosts would need a `staging` profile of the gateway's
products (`config/pg-api-gateway,staging/`) and of pv-auth-service's `origins`.

## Where each kind of setting lives

| What | Where | Example |
|---|---|---|
| Application configuration, same in every environment | Consul KV, this repository (`config/<service>/data`); a profile's differences in `config/<service>,<profile>/` | routes, RBAC, issuer, brand, quotas, timeouts |
| Deployment facts | the chart's `values-<env>.yaml` | replicas, resources, image registry, ingress hosts, database host, Redis address |
| What a service is, same everywhere | the chart's `values.yaml` | ports, Key Vault secret names, network peers |
| Secrets | Key Vault → ExternalSecret → Kubernetes Secret → env | `CONSUL_TOKEN`, client secrets, database passwords, signing key |

`SPRING_PROFILES_ACTIVE` is the environment name (`staging`, `prod`), so
`config/<service>,staging/data` can hold an environment's differences when one is really needed. No
secret ever goes into values files or Consul (`scripts/validate.py` refuses secret-looking values here).

## Namespaces

One namespace per product; the platform's are fixed because other charts name them.

| Namespace | What runs there | Consul names |
|---|---|---|
| `pg-api-gateway` | pg-api-gateway, the edge Ingress and its certificates | `pg-api-gateway` |
| `runvance-platform` | pv-auth-service, the platform Redis (and the superseded `runvance-platform` chart until it is removed) | `pv-auth-service` |
| `notification` | Runvance Notify: notification-service, notification-web | `notification-service`, `notification-web` |
| `runvance-ai` | runvance-ai-service | `ai-service` |
| `consul` | Consul servers and agents | |
| `peoplevizio` | Runvance Payroll | `payroll-api`, `peoplevizio-web` |
| `interviewai` | Runvance Interview | `interview-api`, `interview-web` |
| `leados` | Runvance CRM | `leados-api`, `leados-web` |
| `demoagent` | Runvance Connect (AI Demo Agent) | `demoagent-platform`, `demoagent-admin`, `demoagent-web` |
| `meet` | Runvance Meet | `meet-api`, `meet-web` |
| `hms` | Runvance Health | `hms-api`, `hms-web` |
| `runvance` | Runvance Accelerator | `runvance-accelerator` |

Consul's catalog sync reads only the namespaces in `syncCatalog.k8sAllowNamespaces`
(`deploy/consul-values.yaml`); a product whose web app is synced must be listed there.

## Labels

The library puts these on everything; product charts that select another service's pods use `app`.

| Label | Value |
|---|---|
| `app` | the service name (`pg-api-gateway`, `pv-auth-service`): what NetworkPolicies of other charts select |
| `app.kubernetes.io/name`, `app.kubernetes.io/instance` | name and release: the selector (never changes after the first install) |
| `app.kubernetes.io/component` | `api`, `web`, `worker`, `gateway` |
| `app.kubernetes.io/part-of` | the product: `runvance-platform`, `runvance-notify`, `payroll`, ... |
| `app.kubernetes.io/version` | the image tag |
| `runvance.com/environment` | `staging` / `prod` |

## Images

- One repository per service in the environment's ACR, named after the service (`pg-api-gateway`,
  `pv-auth-service`, `notification-service`, `notification-web`) unless the chart says otherwise.
- Tagged with the git commit SHA (short), built by hand with `az acr build` (`scripts/release.sh
  <service> <env> --build`); `latest` is refused by the library. The tag reaches the chart through the
  service's release file (`deploy/releases/<env>/<service>.yaml`: `image.tag`, or `common.image.tag`
  for several workloads); without one the chart's `appVersion` is the tag, and charts without an
  appVersion refuse to render.
- Images run as UID 10001 and must work on a read-only root file system with only `/tmp` writable.
  The cluster pulls from ACR with its kubelet identity (`az aks update --attach-acr`), so charts need
  no pull secrets.

## Secrets and the Consul token

Key Vault secret names are `<service>-<purpose>`; a value two services share has one name, used by
both charts. The chart lists names only.

| Key Vault secret | Env var | Used by |
|---|---|---|
| `<service>-consul-token` | `CONSUL_TOKEN` | every service that talks to Consul |
| `pv-gateway-client-secret` | `GATEWAY_CLIENT_SECRET` / `AUTH_GATEWAY_CLIENT_SECRET` | pg-api-gateway, pv-auth-service |
| `runvance-redis-password` | `REDIS_PASSWORD` | pg-api-gateway (and the products that use the platform Redis) |
| `pv-auth-service-db-password`, `-signing-key` | `AUTH_DB_PASSWORD`, `AUTH_SIGNING_KEY_PEM` | pv-auth-service |
| `pv-auth-service-notify-api-key` (optional) | `AUTH_NOTIFY_API_KEY` | pv-auth-service |
| `pv-auth-service-bootstrap-admin-{email,password}`, `-bootstrap-client-{id,secret}` (optional, once) | `AUTH_BOOTSTRAP_*` | pv-auth-service |
| `notification-service-mongodb-uri`, `-encryption-key` | `MONGODB_URI`, `APP_ENCRYPTION_KEY` | notification-service |
| `notification-service-sendgrid-api-key`, `-sendgrid-webhook-key`, `-legacy-jwt-secret` (optional) | `SENDGRID_*`, `JWT_SECRET` | notification-service |

How a service gets its Consul token, once per environment:

1. Add its `spring.application.name` to `consul/services.txt`.
2. With a management token (consul-k8s keeps the bootstrap token in the `consul` namespace's
   `consul-bootstrap-acl-token` Secret) and `CONSUL_HTTP_ADDR` pointing at the cluster's Consul (e.g.
   through `kubectl -n consul port-forward svc/consul-server`):
   `CONSUL_SECRETS_DIR=<an empty directory> scripts/bootstrap-acl.sh --tokens`. It creates the
   service's least-privilege policy and token in `<that directory>/<service>.token`. Without
   `CONSUL_SECRETS_DIR` it uses `secrets/`, where the local Consul's tokens already are: the script
   keeps a token whose file exists, so the cluster would get none of its own. A file lost before
   step 3 is read back from Consul on the next run (no second token).
3. `az keyvault secret set --vault-name <vault> -n <service>-consul-token --file <that directory>/<service>.token`,
   then delete the local file once Key Vault lists it. One exception: Payroll's chart reads
   `peoplevizio-consul-token` (its `secrets.consulToken`) for the `payroll-api` token.
4. The chart's ExternalSecret (`<service>-secrets`) picks it up within its refresh interval (1 h, or
   annotate the ExternalSecret `force-sync=<timestamp>`); a running pod reads secrets at start only, so
   `kubectl rollout restart` after a rotation.

Optional groups (`secrets.optional`) are separate Secrets mounted `optional: true`: a missing Key Vault
entry leaves only that feature off (no Notify key: pv-auth sends nothing) instead of stopping the pod.

## Network

Every workload gets its own NetworkPolicy (ingress and egress), and each product namespace one
`default-deny`:

- **In.** pg-api-gateway's pods (`networkPolicy.fromGateway`) for anything it routes to; named
  internal callers by namespace and their pods' labels (`callers`); Consul's health checks and
  Prometheus on the management port. Only the gateway accepts traffic from the ingress controller.
  The `X-PV-*` identity headers are trusted because nothing else can reach a product.
- **Out.** DNS, Consul, the service's own databases (`databases`), the services it calls (`services`,
  by namespace and labels), and only where a service calls providers, the public internet on 443
  (`internet`), never private ranges or the instance metadata service.
- **A new caller needs both sides:** an `egress.services` entry in the caller's chart and a `callers`
  entry in the callee's (product backends calling pv-auth-service's token endpoint, ai-service or
  Notify). A service call still needs a pv-auth-service service token; the network only narrows who
  can try.

## Service discovery

Spring services register themselves (Spring Cloud Consul) at their pod address with their readiness
check; pg-api-gateway and other callers use `lb://<name>`. Services that aren't Spring (web apps, the
demo browser worker) set `consul.catalogSync.enabled`, and Consul's catalog sync registers their
Service. A Spring service's Service is annotated `service-sync: "false"` so it is never in the
catalog twice.

## The edge

pg-api-gateway alone has an Ingress (the library refuses one anywhere else): `*.runvance.com`,
`accounts.runvance.com` and `api.interview.runvance.com` (two labels deep, so neither the wildcard
host rule nor the wildcard certificate covers it), all to the gateway's Service. The gateway answers
the hosts its configuration lists (`config/pg-api-gateway/conf.d`) and 404s the rest. Certificates
are cert-manager `Certificate`s: the wildcard through a DNS-01 ClusterIssuer (`letsencrypt-dns01`,
with write access to the `runvance.com` Azure DNS zone), single hosts through HTTP-01
(`letsencrypt-prod`). DNS: `*.runvance.com`, `accounts.runvance.com` and `api.interview.runvance.com`
point at the app-routing load balancer of the environment that owns the hosts; `www` and the apex
keep their own records. The zone, every record, the move from Namecheap and the DNS-01 issuer:
[dns.md](dns.md).

## Add a service

1. Its Consul name in `consul/services.txt`, its token in Key Vault (above), its configuration in
   `config/<service>/` (and gateway routes in `config/pg-api-gateway/conf.d/<product>.yml` if public).
2. `deploy/helm/<service>/` in its repository: `Chart.yaml` depending on `runvance-service`, a
   template with `{{ include "runvance-service.all" . }}`, `values.yaml` (name, ports, Key Vault names,
   callers, egress), `values-staging.yaml` and `values-prod.yaml` (deployment facts only). See the
   library's README for a minimal chart.
3. Its callers' charts admit it (egress) and the charts it calls admit it (`callers`); if it is public,
   add its namespace to pg-api-gateway's `networkPolicy.egress.services`.
4. `deploy/charts/check.sh <its chart dir>` here; then the service in `deploy/services.yaml`, an
   entry in each `deploy/argocd/applicationset-<env>.yaml` and a release file per environment
   (`chartRevision: unreleased` until the first release). It deploys only by hand from Argo CD:
   [deploy-with-argocd.md](deploy-with-argocd.md). Nothing runs `helm upgrade` against a cluster.

Install order on a new cluster: Consul, External Secrets and the store, cert-manager and issuers,
then pv-auth-service, notification-service, pg-api-gateway, then products.

## Migrating the Container Apps products

**Status (later on 9 November 2026):** steps 1–4 are done in each product's working tree: charts
on the library (`leados`, `demoagent`, `meet`, `hms`, `runvance-accelerator`; Payroll's
`peoplevizio` and Interview's `interview-ai` moved onto it too, keeping their names and selectors),
Key Vault names listed in each chart's values, configuration in `config/<service>/`, and the
products' pods admitted by ai-service, pv-auth-service and notification-service. The deploy
scripts are deleted or parked in a "do not run" folder and every GitHub workflow is gone (no CI/CD:
releases are built by hand and deployed by a manual Argo CD Sync, [deploy-with-argocd.md](deploy-with-argocd.md)).
Step 5, the cut-over itself, is the owner's, per service.

Runvance CRM (`leados-api`, `PayVizio-LeadOS/infra/deploy-azure.sh`), Connect and Meet
(`ai-demo-agent/infra/azure/deploy.sh`, `meet.sh`: `demoagent-platform`, the browser worker,
`demoagent-web`, `-admin`, `meet-api`, `meet-web`), Health (`hms/infra/azure/main.bicep`) and the
Accelerator (App Service) move the same way, one product at a time:

1. **Chart.** `deploy/helm/<product>/` on the library: one workload per container app (API, web,
   worker, admin) in one chart with `common:`. Ports, probes and resources from today's container app;
   replicas stay 1 where the app keeps state in memory (Connect's session hub and browser worker,
   Meet) and say so in the values, as pv-auth-service and Notify do.
2. **Secrets.** Every `secretref:` of the container app becomes a Key Vault entry named
   `<service>-<purpose>` and a `secrets.env` key. The product keeps no AI or mail provider keys
   (ai-service and Notify hold them); its service-client secrets for `ai.invoke` / `comm.send` come
   along.
3. **Configuration.** Each `--set-env-vars` value that isn't a deployment fact moves to
   `config/<service>/data` here; the container's environment keeps only the database host and
   similar facts.
4. **Network.** Namespace `leados`, `demoagent`, `meet`, `hms` or `runvance` (already in the gateway's
   egress and Consul's catalog sync list). Add the product's pods to the `callers` of ai-service,
   pv-auth-service and notification-service, and its databases to its own egress (Cosmos DB for
   MongoDB on 10255, Event Hubs' Kafka endpoint on 9093, Azure Redis on 6380 as `cidrs` or
   `databases`). Drop the `cidrCallers` entries that admitted its Container Apps subnet.
5. **Cut over.** Deploy to the cluster with no traffic, check it registers in Consul and passes its
   checks, then the gateway already routes `lb://<service>` to it; remove the container app's
   external ingress and custom domain, then the container app, its environment and the deploy
   script and workflow.

## Open items before the first deploy

- **ACR for charts**: decided, `oci://acrinterviewaistaging.azurecr.io/helm` (both environments: Argo CD
  fetches it, not the clusters); the platform charts point there already, product charts change one
  line ([deploy-with-argocd.md](deploy-with-argocd.md), "The library chart"). Still to do once:
  `helm push` the library there and the Argo CD credential.
- **Key Vault entries** in the table above (none of the new names exist yet), and the `pv_auth`
  database and role on the Flexible Server.
- **TLS and DNS**: the `letsencrypt-dns01` ClusterIssuer (DNS-01 with the runvance.com zone), DNS
  records for the wildcard, accounts and api.interview hosts, and the superseded `runvance-platform`
  chart's `edge` Ingress removed for the hosts this chart takes over (two Ingresses for one host
  conflict).
- **Consul over TLS**: `deploy/consul-values.yaml` turns TLS on, and the consul-k8s chart then
  serves HTTPS only (8501) unless `global.tls.httpsOnly: false`. The charts use HTTP on 8500 (as
  ai-service and Payroll do); either allow HTTP inside the cluster or switch them to `consul.scheme:
  https`, port 8501, with Consul's CA in each JVM's trust store.
- **Platform Redis**: its NetworkPolicy (in the `runvance-platform` chart) admits only that chart's
  old gateway and Payroll; it must admit `pg-api-gateway` (and `runvance-ai`).
- **Notify**: `config/notification-service/data.yml` with its public URLs, platform tenant id and
  sender; the dashboard image on an unprivileged nginx (port 8080) before `web.enabled`; a claim in
  its queue worker before a second replica.

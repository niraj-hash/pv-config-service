# runvance-service (Helm library chart, 0.1.0)

Every Runvance service on AKS ships a chart built on this library (platform contract, "Runtime").
A service chart has a `Chart.yaml` that depends on it, a one-line template, and values; the library
renders the rest the same way for everyone. Conventions around it (namespaces, image tags, Key Vault
names, adding a service): [docs/runtime.md](../../../docs/runtime.md).

## What it renders, per workload

| Resource | Default |
|---|---|
| Deployment | non-root (UID 10001), read-only root file system with a writable `/tmp`, all capabilities dropped, seccomp `RuntimeDefault`; rolling update one pod at a time (`maxSurge 1`, `maxUnavailable 0`); spread over zones and nodes; startup/readiness/liveness probes on actuator (`/actuator/health/liveness`, `/readiness`) on the management port; a 5 s `preStop` so callers drop the pod first; no service links, no Kubernetes API token |
| Platform env | `CONFIG_IMPORT=consul:`, `SPRING_PROFILES_ACTIVE` (= `environment` unless `spring.profiles`), `CONSUL_HOST`/`CONSUL_PORT`, `SPRING_CLOUD_CONSUL_DISCOVERY_IP_ADDRESS` = the pod IP, `JAVA_TOOL_OPTIONS`; a chart's own `env` wins on the same name |
| ExternalSecret(s) | Key Vault → `<name>-secrets` (the Consul token as `CONSUL_TOKEN`, plus `secrets.env`), mounted with `envFrom`; each `secrets.optional.<group>` its own `<name>-<group>` Secret, mounted optional |
| ServiceAccount | `azure.workload.identity/client-id` (and the pod label `azure.workload.identity/use`) when `serviceAccount.workloadIdentity.clientId` is set |
| Service | ClusterIP port 80 → `http`; `consul.hashicorp.com/service-sync: "false"` for Spring services (they register themselves), `"true"` plus the service name with `consul.catalogSync.enabled` |
| HPA | CPU 70 %, `replicas`…4; the Deployment then leaves `replicas` to it |
| PDB | `minAvailable: 1`, only when at least two replicas run (one replica: none, so drains never block) |
| NetworkPolicy | in: pg-api-gateway (`fromGateway`), named `callers` (namespace + pod labels), `cidrCallers`, Consul's health checks and Prometheus on the management port; out: DNS, Consul, `databases`, named `services`, `cidrs`, optionally the public internet on 443 (never private ranges or the metadata service). Optional namespace-wide `default-deny` |
| Prometheus | `prometheus.io/scrape`, `port` (management), `path` `/actuator/prometheus` |
| Ingress + Certificates | only for `pg-api-gateway` (any other name fails the render); cert-manager `Certificate`s per TLS secret, each with its own issuer; a NetworkPolicy for HTTP-01 solver pods |

Every key and its default is in [templates/_defaults.tpl](templates/_defaults.tpl). A chart's values
are deep-merged over them (maps key by key; lists, scalars, `false` and `""` replace), so a chart sets
only what differs. Guards: `name` must be a DNS label, `image.tag: latest` is refused, a caller
without pod labels is refused, an Ingress anywhere but the gateway is refused.

## Minimal example

```yaml
# deploy/helm/payroll-api/Chart.yaml
apiVersion: v2
name: payroll-api
type: application
version: 0.1.0
appVersion: "0.1.0"
dependencies:
  - name: runvance-service
    version: 0.1.0
    repository: oci://<acr>.azurecr.io/helm   # before it is published: file://<path to this directory>
```

```yaml
# deploy/helm/payroll-api/templates/payroll-api.yaml
{{ include "runvance-service.all" . }}
```

```yaml
# deploy/helm/payroll-api/values.yaml
name: payroll-api                  # its spring.application.name: Consul service, ACL token, config key
partOf: payroll
namespace: peoplevizio
image: { repository: hrms-api }
secrets:
  env: { DB_PASS: peoplevizio-db-password }   # ENV_VAR: Key Vault secret name
env:
  DB_HOST: psql-interviewai-staging.postgres.database.azure.com
networkPolicy:
  egress:
    databases: [{ name: postgres, port: 5432 }]
    services: [{ name: ai-service, namespace: runvance-ai, labels: { app: ai-service }, ports: [8080] }]
```

```yaml
# deploy/helm/payroll-api/values-staging.yaml
environment: staging
image: { registry: acrinterviewaistaging.azurecr.io }
replicas: 2
resources: { requests: { cpu: 500m, memory: 1536Mi }, limits: { memory: 2Gi } }
```

That renders a ServiceAccount, the ExternalSecret (`CONSUL_TOKEN` from `payroll-api-consul-token`,
`DB_PASS`), the Deployment, the Service, an HPA (2–4), a PDB and a NetworkPolicy admitting only
pg-api-gateway, Consul and Prometheus. Deploy with `--set image.tag=<git sha>`.

## More than one workload in a chart

Pass `(dict "root" $ "values" .Values.<key>)`; a chart's `common:` map applies to every workload:

```yaml
{{ include "runvance-service.all" . }}                                        # the API (top-level values)
{{ include "runvance-service.all" (dict "root" $ "values" .Values.web) }}     # the web app
```

```yaml
common: { partOf: runvance-notify, namespace: notification, environment: staging }
name: notification-service
web:
  name: notification-web
  component: web
  spring: { enabled: false }
  consul: { enabled: false, catalogSync: { enabled: true } }   # registered by catalog sync, as lb://notification-web
  probes: { startup: { path: / }, readiness: { path: / }, liveness: { path: / } }
```

Each resource also has its own template (`runvance-service.deployment`, `.service`,
`.serviceAccount`, `.externalSecrets`, `.hpa`, `.pdb`, `.networkPolicy`, `.ingress`), taking the same
argument, for a chart that renders some itself.

## Values at a glance

| Group | Keys |
|---|---|
| identity | `name` (required), `component`, `partOf`, `environment`, `namespace`, `labels`, `annotations`, `enabled` |
| image | `image.registry`, `.repository` (default `name`), `.tag` (default appVersion), `.digest`, `.pullPolicy`, `imagePullSecrets` |
| rollout | `replicas`, `strategy`, `minReadySeconds`, `terminationGracePeriodSeconds`, `preStopSleepSeconds`, `autoscaling.*`, `pdb.*` |
| container | `ports.http`, `ports.management` (0: same port), `extraPorts`, `command`, `args`, `resources`, `probes.{startup,readiness,liveness}` (`path`, timings, or a whole `spec`), `securityContext`, `podSecurityContext`, `tmpVolume`, `extraVolumes`, `extraVolumeMounts`, `extraInitContainers`, `extraContainers` |
| environment | `env` (name: value, or name: `{valueFrom}`), `extraEnv`, `extraEnvFrom`, `spring.{enabled,configImport,profiles,javaToolOptions}` |
| Consul | `consul.{enabled,host,port,scheme,namespace,registerPodIp,serviceName}`, `consul.catalogSync.{enabled,tags}` |
| secrets | `secrets.store`, `.refreshInterval`, `.consulToken` (default `<name>-consul-token`), `.env`, `.optional.<group>` |
| identity in Azure | `serviceAccount.{create,name,annotations,automountToken}`, `serviceAccount.workloadIdentity.{clientId,tenantId}` |
| network | `networkPolicy.{enabled,namespaceDefaultDeny,fromGateway,gateway,callers,cidrCallers,monitoring,extraIngress}`, `networkPolicy.egress.{dns,databases,services,cidrs,internet,extra}` |
| scheduling | `topologySpread`, `nodeSelector`, `tolerations`, `affinity`, `priorityClassName`, `podLabels`, `podAnnotations` |
| metrics | `metrics.{enabled,path}` |
| edge (gateway only) | `ingress.{enabled,allowedFor,className,annotations,hosts,tls,certificates,controller,acmeHttp01Solver}` |

## Depending on it

**Published (real deployments).** The library lives in ACR as an OCI chart; products pin a version:

```bash
helm package deploy/charts/runvance-service                     # runvance-service-0.1.0.tgz
az acr login -n <acr>                                           # or: helm registry login <acr>.azurecr.io
helm push runvance-service-0.1.0.tgz oci://<acr>.azurecr.io/helm
```

```yaml
dependencies:
  - { name: runvance-service, version: "~0.1.0", repository: "oci://<acr>.azurecr.io/helm" }
```

The registry is `oci://acrinterviewaistaging.azurecr.io/helm` (both environments; Argo CD's repo-server
fetches it with its Helm OCI credential and runs `helm dependency build` from the chart's
`Chart.lock`; docs/deploy-with-argocd.md, "The library chart"). `helm dependency update` resolves it
and writes `Chart.lock`; commit the lock, never `charts/`. Versions: patch for fixes, minor for new keys with defaults that render the same
manifests as before, major for anything that changes existing output in a way that matters (renamed
keys, selector labels, default ports). A published version is never overwritten.

**Before it is published, and offline.** Point the dependency at this directory instead:
`repository: file://../../../../pv-config-service/deploy/charts/runvance-service` (from
`<repo>/deploy/helm/<chart>`, with pv-config-service checked out beside the repo). `helm dependency
build` copies a snapshot into the chart's `charts/` (git-ignored), so after changing the library run
it again. A repository without the sibling checkout can vendor the package instead: `helm package`
this directory into its chart's `charts/` and commit the `.tgz`; `helm lint` and `helm template` use
whatever is in `charts/` and fetch nothing.

**Checking.** `deploy/charts/check.sh` lints this chart and, for each chart built on it (the platform
charts by default, or the directories given), builds the dependency, runs `helm lint --strict` and
`helm template` with every `values-<env>.yaml`, and `kubeconform` on the output when installed.

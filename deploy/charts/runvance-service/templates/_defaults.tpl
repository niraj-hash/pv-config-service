{{/*
The library's defaults: every key a service chart may set, with its default. A chart's values are
deep-merged over these (maps key by key; lists, scalars, false and "" replace), after the chart's
`common:` map, so a chart sets only what differs. README.md documents each group.
*/}}
{{- define "runvance-service.defaults" -}}
# Render this workload at all.
enabled: true
# Required. Kubernetes name of everything rendered, the `app` label, and (unless
# consul.serviceName says otherwise) the Consul service name. For a Spring service it is its
# spring.application.name, so pg-api-gateway's lb://<name> and its Consul ACL token match.
name: ""
# app.kubernetes.io/component (api, web, worker) and the container's name.
component: api
# app.kubernetes.io/part-of: the product (runvance-platform, runvance-notify, payroll, ...).
partOf: ""
# staging | prod: label runvance.com/environment, and the Spring profile unless spring.profiles is set.
environment: ""
# Default: the release namespace. One namespace per product (docs/runtime.md).
namespace: ""
labels: {}
annotations: {}

image:
  registry: ""          # e.g. acrinterviewaistaging.azurecr.io
  repository: ""        # default: name
  tag: ""               # default: the chart's appVersion; CI sets the git SHA. Never "latest".
  digest: ""            # sha256:...; wins over tag
  pullPolicy: IfNotPresent
  allowLatest: false
imagePullSecrets: []

replicas: 2
revisionHistoryLimit: 5
minReadySeconds: 0
# One new pod at a time, never below the wanted count (Flyway runs in one pod at a time).
strategy:
  type: RollingUpdate
  rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
terminationGracePeriodSeconds: 45
# Seconds the pod keeps serving after it is told to stop, so Services and the gateway's
# instance lists drop it first. 0: no preStop hook.
preStopSleepSeconds: 5

ports:
  http: 8080            # the application's port (container port "http")
  management: 0         # actuator port ("management"); 0: actuator is on the http port
extraPorts: []          # [{ name, containerPort, protocol }]

command: []
args: []

# Plain environment: NAME: value (strings, numbers, booleans), or NAME: { valueFrom map }.
# Deployment facts only (database host, Redis address); application config is Consul's.
env: {}
extraEnv: []            # raw EnvVar list, rendered last
extraEnvFrom: []        # raw EnvFromSource list

spring:
  enabled: true         # CONFIG_IMPORT, SPRING_PROFILES_ACTIVE, JAVA_TOOL_OPTIONS
  configImport: "consul:"   # fail at start-up without Consul; "optional:consul:" to run without
  profiles: ""          # SPRING_PROFILES_ACTIVE; default: environment
  javaToolOptions: "-XX:MaxRAMPercentage=75"

consul:
  # The service talks to Consul itself (Spring Cloud Consul): CONSUL_HOST/PORT, CONSUL_TOKEN,
  # egress to Consul, Consul's health checks in. Independent of catalogSync below.
  enabled: true
  host: consul-server.consul.svc.cluster.local
  port: 8500
  scheme: http          # https: also SPRING_CLOUD_CONSUL_SCHEME (needs Consul's CA in the JVM trust store)
  namespace: consul     # where Consul runs (NetworkPolicy)
  # Spring services register themselves at the pod address (prefer-ip-address).
  registerPodIp: true
  serviceName: ""       # default: name
  # For services that don't register themselves (web apps, workers that aren't Spring): Consul's
  # catalog sync copies the annotated Service into the catalog (deploy/consul-values.yaml; the
  # namespace must be in syncCatalog.k8sAllowNamespaces).
  catalogSync:
    enabled: false
    tags: []

secrets:
  # External Secrets Operator: Key Vault -> one Kubernetes Secret per group -> envFrom.
  store: { name: azure-keyvault, kind: ClusterSecretStore }
  refreshInterval: 1h
  # Key Vault secret holding this service's Consul ACL token (CONSUL_TOKEN), made by
  # scripts/bootstrap-acl.sh. "": <name>-consul-token. Ignored when consul.enabled is false.
  consulToken: ""
  # Required: ENV_VAR: key-vault-secret-name (or { key, property, version }). Secret <name>-secrets.
  env: {}
  # Optional groups: <group>: { ENV_VAR: key-vault-secret-name }. Each is its own ExternalSecret
  # and Secret <name>-<group>, mounted optional: a missing Key Vault entry leaves only that group
  # out (the feature it serves stays off) instead of stopping the pod.
  optional: {}

serviceAccount:
  create: true
  name: ""              # default: name
  annotations: {}
  automountToken: false # Spring services don't call the Kubernetes API
  workloadIdentity:
    clientId: ""        # Azure workload identity: the user-assigned identity's client id
    tenantId: ""

service:
  enabled: true
  type: ClusterIP
  port: 80
  annotations: {}
  extraPorts: []        # [{ name, port, targetPort, protocol }]

podLabels: {}
podAnnotations: {}
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  seccompProfile: { type: RuntimeDefault }
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: [ALL] }
# Writable /tmp (Tomcat and Netty need it on a read-only root file system).
tmpVolume:
  enabled: true
  sizeLimit: 256Mi
extraVolumes: []
extraVolumeMounts: []
extraInitContainers: []
extraContainers: []

resources:
  requests: { cpu: 100m, memory: 512Mi }
  limits: { memory: 1Gi }

# Probes: an httpGet on the management port, or `spec:` (a complete probe) instead.
probes:
  startup:   { enabled: true, path: /actuator/health/liveness,  periodSeconds: 5,  timeoutSeconds: 3, failureThreshold: 36 }
  readiness: { enabled: true, path: /actuator/health/readiness, periodSeconds: 10, timeoutSeconds: 3, failureThreshold: 3 }
  liveness:  { enabled: true, path: /actuator/health/liveness,  periodSeconds: 20, timeoutSeconds: 3, failureThreshold: 3 }

# Prometheus scrape annotations (management port).
metrics:
  enabled: true
  path: /actuator/prometheus

autoscaling:
  enabled: true
  minReplicas: 0        # 0: replicas
  maxReplicas: 4
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 0   # 0: not a metric
  behavior:
    scaleDown: { stabilizationWindowSeconds: 300 }

pdb:
  enabled: true
  minAvailable: 1
  maxUnavailable: ""    # set to use maxUnavailable instead of minAvailable
  # Off for a single replica: minAvailable 1 there would block every node drain.
  singleReplica: false

# Spread pods over zones and nodes when the cluster can (ScheduleAnyway).
topologySpread:
  enabled: true
  zone: true
  hostname: true
nodeSelector: {}
tolerations: []
affinity: {}
priorityClassName: ""

networkPolicy:
  enabled: true
  # A namespace-wide default-deny (Ingress and Egress) named default-deny. Exactly one chart per
  # namespace renders it; this workload's own policy denies everything it doesn't allow anyway.
  namespaceDefaultDeny: false
  # In: pg-api-gateway (a service the gateway routes to). False for internal-only services.
  fromGateway: true
  gateway:
    namespace: pg-api-gateway
    labels: { app: pg-api-gateway }
  # In: named internal callers, by namespace and their pods' labels (the labels their own charts
  # set). [{ name, namespace, labels, ports }]; ports default to the http port.
  callers: []
  # In: callers outside the cluster, by address (Container Apps subnets). [{ name, cidr, ports }]
  cidrCallers: []
  monitoring:
    enabled: true
    namespace: monitoring
    labels: {}
  extraIngress: []      # raw NetworkPolicyIngressRule list
  egress:
    enabled: true
    dns:
      enabled: true
      namespace: kube-system
      labels: { k8s-app: kube-dns }
    # Its databases, outside the cluster: [{ name, port, cidr }]; no cidr: that port anywhere.
    databases: []
    # Services it calls: [{ name, namespace, labels, ports }]; no labels: every pod there; no
    # ports: any port. Namespace defaults to its own.
    services: []
    # Outside addresses: [{ name, cidr, except, ports }].
    cidrs: []
    # The internet (provider APIs): any public address on these ports, never private ranges or
    # the instance metadata service.
    internet:
      enabled: false
      ports: [443]
      except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16]
    extra: []           # raw NetworkPolicyEgressRule list

# The edge. Only pg-api-gateway has one (platform contract): any other name fails the render.
ingress:
  enabled: false
  allowedFor: [pg-api-gateway]
  className: webapprouting.kubernetes.azure.com
  annotations: {}
  hosts: []             # every host is sent to this Service, path /
  tls: []               # [{ secretName, hosts }]
  # cert-manager Certificates for the tls secrets: [{ name, secretName, dnsNames, issuer, issuerKind }].
  # Prefer these to the ingress-shim annotation: each certificate can use its own issuer (a
  # wildcard needs DNS-01; a single host can use HTTP-01).
  certificates: []
  # The ingress controller's namespace: the only source admitted to the edge's http port.
  controller:
    namespace: app-routing-system
    labels: {}
  # Admit the ingress controller to cert-manager's HTTP-01 solver pods in this namespace.
  acmeHttp01Solver: false
{{- end -}}

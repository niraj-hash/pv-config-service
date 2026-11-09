{{/* Service, ServiceAccount, ExternalSecrets, HPA, PDB. */}}

{{- define "runvance-service.service" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if and $v.enabled $v.service.enabled -}}
{{- $c := dict "v" $v "root" $root -}}
{{- $annotations := deepCopy ($v.service.annotations | default dict) -}}
{{- if $v.consul.catalogSync.enabled -}}
{{- $_ := set $annotations "consul.hashicorp.com/service-sync" "true" -}}
{{- $_ := set $annotations "consul.hashicorp.com/service-name" $v._d.consulServiceName -}}
{{- $_ := set $annotations "consul.hashicorp.com/service-port" "http" -}}
{{- with $v.consul.catalogSync.tags -}}
{{- $_ := set $annotations "consul.hashicorp.com/service-tags" (join "," .) -}}
{{- end -}}
{{- else if $v.consul.enabled -}}
{{- /* A Spring service registers itself: catalog sync must never add a second entry, whatever its default. */ -}}
{{- $_ := set $annotations "consul.hashicorp.com/service-sync" "false" -}}
{{- end }}
---
apiVersion: v1
kind: Service
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c "annotations" $annotations) | nindent 2 }}
spec:
  type: {{ $v.service.type }}
  selector:
    {{- include "runvance-service.selectorLabels" $c | nindent 4 }}
  ports:
    - { name: http, port: {{ $v.service.port }}, targetPort: http, protocol: TCP }
    {{- with $v.service.extraPorts }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end }}
{{- end -}}

{{- define "runvance-service.serviceAccount" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if and $v.enabled $v.serviceAccount.create -}}
{{- $c := dict "v" $v "root" $root -}}
{{- $annotations := deepCopy ($v.serviceAccount.annotations | default dict) -}}
{{- with $v.serviceAccount.workloadIdentity.clientId -}}
{{- $_ := set $annotations "azure.workload.identity/client-id" . -}}
{{- end -}}
{{- with $v.serviceAccount.workloadIdentity.tenantId -}}
{{- $_ := set $annotations "azure.workload.identity/tenant-id" . -}}
{{- end }}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c "name" $v._d.serviceAccountName "annotations" $annotations) | nindent 2 }}
automountServiceAccountToken: {{ $v.serviceAccount.automountToken }}
{{- end }}
{{- end -}}

{{/* One ExternalSecret: Key Vault entries -> a Kubernetes Secret whose keys are env var names. */}}
{{- define "runvance-service.externalSecretResource" -}}
{{- $v := .c.v }}
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  {{- include "runvance-service.metadata" (dict "c" .c "name" .name) | nindent 2 }}
spec:
  refreshInterval: {{ $v.secrets.refreshInterval }}
  secretStoreRef: { name: {{ $v.secrets.store.name }}, kind: {{ $v.secrets.store.kind }} }
  target:
    name: {{ .name }}
    creationPolicy: Owner
  data:
    {{- range $env, $ref := .items }}
    {{- if kindIs "map" $ref }}
    - secretKey: {{ $env }}
      remoteRef:
        {{- toYaml $ref | nindent 8 }}
    {{- else }}
    - { secretKey: {{ $env }}, remoteRef: { key: {{ $ref | quote }} } }
    {{- end }}
    {{- end }}
{{- end -}}

{{- define "runvance-service.externalSecrets" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if $v.enabled -}}
{{- $c := dict "v" $v "root" $root -}}
{{- if $v._d.hasSecrets -}}
{{- $items := deepCopy ($v.secrets.env | default dict) -}}
{{- if $v.consul.enabled -}}{{- $_ := set $items "CONSUL_TOKEN" $v._d.consulTokenKey -}}{{- end -}}
{{- include "runvance-service.externalSecretResource" (dict "c" $c "name" $v._d.secretName "items" $items) }}
{{- end }}
{{- range $group, $items := $v.secrets.optional }}
{{- if not (regexMatch "^[a-z][-a-z0-9]*$" $group) -}}
{{- fail (printf "runvance-service: secrets.optional group %q must be lower-case letters, digits and dashes" $group) -}}
{{- end }}
{{- include "runvance-service.externalSecretResource" (dict "c" $c "name" (printf "%s-%s" $v.name $group) "items" $items) }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "runvance-service.hpa" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if and $v.enabled $v.autoscaling.enabled -}}
{{- $c := dict "v" $v "root" $root }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c) | nindent 2 }}
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: {{ $v.name }} }
  minReplicas: {{ $v._d.minReplicas }}
  maxReplicas: {{ $v.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: {{ $v.autoscaling.targetCPUUtilizationPercentage }} }
    {{- if $v.autoscaling.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target: { type: Utilization, averageUtilization: {{ $v.autoscaling.targetMemoryUtilizationPercentage }} }
    {{- end }}
  {{- with $v.autoscaling.behavior }}
  behavior:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
{{- end -}}

{{- define "runvance-service.pdb" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if and $v.enabled $v.pdb.enabled (or (ge (int $v._d.minReplicas) 2) $v.pdb.singleReplica) -}}
{{- $c := dict "v" $v "root" $root }}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c) | nindent 2 }}
spec:
  {{- if ne (toString $v.pdb.maxUnavailable) "" }}
  maxUnavailable: {{ $v.pdb.maxUnavailable }}
  {{- else }}
  minAvailable: {{ $v.pdb.minAvailable }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "runvance-service.selectorLabels" $c | nindent 6 }}
{{- end }}
{{- end -}}

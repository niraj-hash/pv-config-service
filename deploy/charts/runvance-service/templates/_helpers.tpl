{{/*
Every public template takes either the chart's root context (the chart's .Values describe the
workload) or (dict "root" $ "values" .Values.web) for a second workload in the same chart. Either
way the chart's `common:` map applies first.
*/}}

{{/* Deep merge .src into .dst (mutates .dst): maps key by key; anything else replaces, false and "" included. */}}
{{- define "runvance-service.deepMerge" -}}
{{- $dst := .dst -}}
{{- range $k, $v := .src -}}
{{- if and (kindIs "map" $v) (kindIs "map" (index $dst $k)) -}}
{{- include "runvance-service.deepMerge" (dict "dst" (index $dst $k) "src" $v) -}}
{{- else -}}
{{- $_ := set $dst $k $v -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* The workload's merged values, as YAML (callers: fromYaml), with derived settings under _d. */}}
{{- define "runvance-service.values" -}}
{{- $root := . -}}
{{- $own := .Values -}}
{{- if hasKey . "root" -}}
{{- $root = .root -}}
{{- $own = .values | default dict -}}
{{- end -}}
{{- $v := fromYaml (include "runvance-service.defaults" $root) -}}
{{- with $root.Values.common -}}
{{- include "runvance-service.deepMerge" (dict "dst" $v "src" (deepCopy .)) -}}
{{- end -}}
{{- include "runvance-service.deepMerge" (dict "dst" $v "src" (deepCopy $own)) -}}
{{- $name := required "runvance-service: `name` is required (the service's spring.application.name)" $v.name -}}
{{- if not (regexMatch "^[a-z]([-a-z0-9]{0,51}[a-z0-9])?$" $name) -}}
{{- fail (printf "runvance-service: name %q must be a DNS label of at most 53 characters" $name) -}}
{{- end -}}
{{- if not $v.namespace -}}{{- $_ := set $v "namespace" $root.Release.Namespace -}}{{- end -}}
{{- $mgmt := int ($v.ports.management | default $v.ports.http) -}}
{{- $minReplicas := int ($v.autoscaling.minReplicas | default $v.replicas) -}}
{{- if and $v.autoscaling.enabled (lt (int $v.autoscaling.maxReplicas) $minReplicas) -}}
{{- fail (printf "runvance-service: %s autoscaling.maxReplicas is below its minimum (%d)" $name $minReplicas) -}}
{{- end -}}
{{- $d := dict
    "httpPort" (int $v.ports.http)
    "mgmtPort" $mgmt
    "mgmtPortName" (ternary "http" "management" (eq $mgmt (int $v.ports.http)))
    "minReplicas" (ternary $minReplicas (int $v.replicas) $v.autoscaling.enabled)
    "serviceAccountName" ($v.serviceAccount.name | default $name)
    "consulServiceName" ($v.consul.serviceName | default $name)
    "consulTokenKey" ($v.secrets.consulToken | default (printf "%s-consul-token" $name))
    "secretName" (printf "%s-secrets" $name)
    "springProfiles" ($v.spring.profiles | default $v.environment)
-}}
{{- $_ := set $d "hasSecrets" (or $v.consul.enabled (gt (len ($v.secrets.env | default dict)) 0)) -}}
{{- $_ := set $v "_d" $d -}}
{{- toYaml $v -}}
{{- end -}}

{{- define "runvance-service.selectorLabels" -}}
app.kubernetes.io/name: {{ .v.name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}

{{- define "runvance-service.labels" -}}
{{ include "runvance-service.selectorLabels" . }}
app: {{ .v.name }}
app.kubernetes.io/component: {{ .v.component }}
{{- with .v.partOf }}
app.kubernetes.io/part-of: {{ . }}
{{- end }}
app.kubernetes.io/version: {{ include "runvance-service.imageTag" . | trunc 63 | trimSuffix "-" | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- with .v.environment }}
runvance.com/environment: {{ . }}
{{- end }}
{{- with .v.labels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "runvance-service.imageTag" -}}
{{- $tag := toString (.v.image.tag | default .root.Chart.AppVersion) -}}
{{- if not $tag -}}{{- fail (printf "runvance-service: %s needs image.tag (or the chart's appVersion)" .v.name) -}}{{- end -}}
{{- if and (eq $tag "latest") (not .v.image.allowLatest) -}}
{{- fail (printf "runvance-service: %s image.tag \"latest\" is not allowed: deploy an immutable tag (the git SHA)" .v.name) -}}
{{- end -}}
{{- $tag -}}
{{- end -}}

{{- define "runvance-service.image" -}}
{{- $repo := .v.image.repository | default .v.name -}}
{{- $ref := $repo -}}
{{- with .v.image.registry }}{{ $ref = printf "%s/%s" (trimSuffix "/" .) $repo }}{{ end -}}
{{- if .v.image.digest -}}
{{- printf "%s@%s" $ref .v.image.digest -}}
{{- else -}}
{{- printf "%s:%s" $ref (include "runvance-service.imageTag" .) -}}
{{- end -}}
{{- end -}}

{{/* metadata: name, namespace, labels (and annotations when given as .annotations). */}}
{{- define "runvance-service.metadata" -}}
name: {{ .name | default .c.v.name }}
namespace: {{ .c.v.namespace }}
labels:
  {{- include "runvance-service.labels" .c | nindent 2 }}
{{- with .annotations }}
annotations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* One probe: an httpGet on the management port, or the given spec. */}}
{{- define "runvance-service.probe" -}}
{{- $p := .p -}}
{{- if $p.spec -}}
{{ toYaml $p.spec }}
{{- else -}}
httpGet:
  path: {{ $p.path }}
  port: {{ $p.port | default .c.v._d.mgmtPortName }}
{{- range $k := list "initialDelaySeconds" "periodSeconds" "timeoutSeconds" "failureThreshold" "successThreshold" }}
{{- if hasKey $p $k }}
{{ $k }}: {{ index $p $k }}
{{- end }}
{{- end }}
{{- end -}}
{{- end -}}

{{/* Selects pods by namespace and labels (a NetworkPolicyPeer). */}}
{{- define "runvance-service.peer" -}}
- namespaceSelector:
    matchLabels: { kubernetes.io/metadata.name: {{ .namespace | quote }} }
  {{- with .labels }}
  podSelector:
    matchLabels:
      {{- toYaml . | nindent 6 }}
  {{- end }}
{{- end -}}

{{- define "runvance-service.ports" -}}
{{- range .ports }}
- { protocol: TCP, port: {{ . }} }
{{- end }}
{{- end -}}

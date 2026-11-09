{{/* NetworkPolicies and the edge Ingress. Kubelet probes need no rule (Cilium admits the node's own traffic). */}}

{{- define "runvance-service.nsPeer" -}}
{{- $peer := dict "namespaceSelector" (dict "matchLabels" (dict "kubernetes.io/metadata.name" .namespace)) -}}
{{- with .labels -}}{{- $_ := set $peer "podSelector" (dict "matchLabels" .) -}}{{- end -}}
{{- toYaml $peer -}}
{{- end -}}

{{- define "runvance-service.tcpPorts" -}}
{{- $out := list -}}
{{- range . -}}{{- $out = append $out (dict "protocol" "TCP" "port" (int .)) -}}{{- end -}}
{{- toYaml (dict "ports" $out) -}}
{{- end -}}

{{- define "runvance-service.networkPolicy" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if and $v.enabled $v.networkPolicy.enabled -}}
{{- $c := dict "v" $v "root" $root -}}
{{- $np := $v.networkPolicy -}}
{{- $d := $v._d -}}
{{- $http := list $d.httpPort -}}
{{- $mgmt := list $d.mgmtPort -}}
{{- $in := list -}}
{{- if $np.fromGateway -}}
{{- $peer := include "runvance-service.nsPeer" $np.gateway | fromYaml -}}
{{- $in = append $in (merge (dict "from" (list $peer)) (include "runvance-service.tcpPorts" $http | fromYaml)) -}}
{{- end -}}
{{- if $v.ingress.enabled -}}
{{- $peer := include "runvance-service.nsPeer" $v.ingress.controller | fromYaml -}}
{{- $in = append $in (merge (dict "from" (list $peer)) (include "runvance-service.tcpPorts" $http | fromYaml)) -}}
{{- end -}}
{{- range $np.callers -}}
{{- $peer := include "runvance-service.nsPeer" (dict "namespace" (required "networkPolicy.callers[] needs a namespace" .namespace) "labels" (required (printf "networkPolicy caller %v needs labels (its pods' labels)" (.name | default .namespace)) .labels)) | fromYaml -}}
{{- $in = append $in (merge (dict "from" (list $peer)) (include "runvance-service.tcpPorts" (.ports | default $http) | fromYaml)) -}}
{{- end -}}
{{- range $np.cidrCallers -}}
{{- $in = append $in (merge (dict "from" (list (dict "ipBlock" (dict "cidr" (required "networkPolicy.cidrCallers[] needs a cidr" .cidr))))) (include "runvance-service.tcpPorts" (.ports | default $http) | fromYaml)) -}}
{{- end -}}
{{- if $v.consul.enabled -}}
{{- /* Consul's HTTP health check decides whether lb://<name> routes here. */ -}}
{{- $peer := include "runvance-service.nsPeer" (dict "namespace" $v.consul.namespace) | fromYaml -}}
{{- $in = append $in (merge (dict "from" (list $peer)) (include "runvance-service.tcpPorts" $mgmt | fromYaml)) -}}
{{- end -}}
{{- if and $np.monitoring.enabled $v.metrics.enabled -}}
{{- $peer := include "runvance-service.nsPeer" $np.monitoring | fromYaml -}}
{{- $in = append $in (merge (dict "from" (list $peer)) (include "runvance-service.tcpPorts" $mgmt | fromYaml)) -}}
{{- end -}}
{{- $in = concat $in ($np.extraIngress | default list) -}}
{{- $out := list -}}
{{- $eg := $np.egress -}}
{{- if $eg.enabled -}}
{{- if $eg.dns.enabled -}}
{{- $peer := include "runvance-service.nsPeer" $eg.dns | fromYaml -}}
{{- $out = append $out (dict "to" (list $peer) "ports" (list (dict "protocol" "UDP" "port" 53) (dict "protocol" "TCP" "port" 53))) -}}
{{- end -}}
{{- if $v.consul.enabled -}}
{{- $peer := include "runvance-service.nsPeer" (dict "namespace" $v.consul.namespace) | fromYaml -}}
{{- $out = append $out (merge (dict "to" (list $peer)) (include "runvance-service.tcpPorts" (uniq (list 8500 8501 (int $v.consul.port))) | fromYaml)) -}}
{{- end -}}
{{- range $eg.databases -}}
{{- $rule := include "runvance-service.tcpPorts" (list (required "networkPolicy.egress.databases[] needs a port" .port)) | fromYaml -}}
{{- with .cidr -}}{{- $_ := set $rule "to" (list (dict "ipBlock" (dict "cidr" .))) -}}{{- end -}}
{{- $out = append $out $rule -}}
{{- end -}}
{{- range $eg.services -}}
{{- $peer := include "runvance-service.nsPeer" (dict "namespace" (.namespace | default $v.namespace) "labels" .labels) | fromYaml -}}
{{- $rule := dict "to" (list $peer) -}}
{{- with .ports -}}{{- $rule = merge $rule (include "runvance-service.tcpPorts" . | fromYaml) -}}{{- end -}}
{{- $out = append $out $rule -}}
{{- end -}}
{{- range $eg.cidrs -}}
{{- $block := dict "cidr" (required "networkPolicy.egress.cidrs[] needs a cidr" .cidr) -}}
{{- with .except -}}{{- $_ := set $block "except" . -}}{{- end -}}
{{- $rule := dict "to" (list (dict "ipBlock" $block)) -}}
{{- with .ports -}}{{- $rule = merge $rule (include "runvance-service.tcpPorts" . | fromYaml) -}}{{- end -}}
{{- $out = append $out $rule -}}
{{- end -}}
{{- if $eg.internet.enabled -}}
{{- $out = append $out (merge (dict "to" (list (dict "ipBlock" (dict "cidr" "0.0.0.0/0" "except" $eg.internet.except)))) (include "runvance-service.tcpPorts" $eg.internet.ports | fromYaml)) -}}
{{- end -}}
{{- $out = concat $out ($eg.extra | default list) -}}
{{- end }}
{{- if $np.namespaceDefaultDeny }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c "name" "default-deny") | nindent 2 }}
spec:
  podSelector: {}
  policyTypes: ["Ingress", "Egress"]
{{- end }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c) | nindent 2 }}
spec:
  podSelector:
    matchLabels:
      {{- include "runvance-service.selectorLabels" $c | nindent 6 }}
  policyTypes: [{{ if $eg.enabled }}"Ingress", "Egress"{{ else }}"Ingress"{{ end }}]
  ingress:
    {{- if $in }}
    {{- toYaml $in | nindent 4 }}
    {{- else }} []
    {{- end }}
  {{- if $eg.enabled }}
  egress:
    {{- if $out }}
    {{- toYaml $out | nindent 4 }}
    {{- else }} []
    {{- end }}
  {{- end }}
{{- if and $v.ingress.enabled $v.ingress.acmeHttp01Solver }}
---
# cert-manager's HTTP-01 solver pods run in this namespace (the Ingress's) while a certificate is
# issued or renewed; the ingress controller must reach them.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c "name" "acme-http01-solver") | nindent 2 }}
spec:
  podSelector:
    matchLabels: { acme.cert-manager.io/http01-solver: "true" }
  policyTypes: ["Ingress"]
  ingress:
    - from:
        {{- include "runvance-service.nsPeer" $v.ingress.controller | fromYaml | list | toYaml | nindent 8 }}
      ports:
        - { protocol: TCP, port: 8089 }
{{- end }}
{{- end }}
{{- end -}}

{{- define "runvance-service.ingress" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if and $v.enabled $v.ingress.enabled -}}
{{- if not (has $v.name $v.ingress.allowedFor) -}}
{{- fail (printf "runvance-service: %s may not have an Ingress: pg-api-gateway is the only public entry (platform contract); route its hosts in config/pg-api-gateway/conf.d instead" $v.name) -}}
{{- end -}}
{{- if not $v.ingress.hosts -}}{{- fail "runvance-service: ingress.hosts is empty" -}}{{- end -}}
{{- $c := dict "v" $v "root" $root }}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c "annotations" $v.ingress.annotations) | nindent 2 }}
spec:
  ingressClassName: {{ $v.ingress.className }}
  {{- with $v.ingress.tls }}
  tls:
    {{- range . }}
    - secretName: {{ required "ingress.tls[] needs a secretName" .secretName }}
      hosts: {{ toJson (required "ingress.tls[] needs hosts" .hosts) }}
    {{- end }}
  {{- end }}
  rules:
    {{- range $v.ingress.hosts }}
    - host: {{ . | quote }}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service: { name: {{ $v.name }}, port: { name: http } }
    {{- end }}
{{- range $v.ingress.certificates }}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c "name" (required "ingress.certificates[] needs a name" .name)) | nindent 2 }}
spec:
  secretName: {{ required "ingress.certificates[] needs a secretName" .secretName }}
  dnsNames: {{ toJson (required "ingress.certificates[] needs dnsNames" .dnsNames) }}
  issuerRef:
    name: {{ required "ingress.certificates[] needs an issuer" .issuer }}
    kind: {{ .issuerKind | default "ClusterIssuer" }}
    group: cert-manager.io
{{- end }}
{{- end }}
{{- end -}}

{{/* Everything for one workload. */}}
{{- define "runvance-service.all" -}}
{{- include "runvance-service.serviceAccount" . }}
{{- include "runvance-service.externalSecrets" . }}
{{- include "runvance-service.deployment" . }}
{{- include "runvance-service.service" . }}
{{- include "runvance-service.hpa" . }}
{{- include "runvance-service.pdb" . }}
{{- include "runvance-service.networkPolicy" . }}
{{- include "runvance-service.ingress" . }}
{{- end -}}

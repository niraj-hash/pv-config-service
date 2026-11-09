{{- define "runvance-service.deployment" -}}
{{- $root := . -}}{{- if hasKey . "root" -}}{{- $root = .root -}}{{- end -}}
{{- $v := include "runvance-service.values" . | fromYaml -}}
{{- if $v.enabled -}}
{{- $c := dict "v" $v "root" $root -}}
{{- $d := $v._d }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  {{- include "runvance-service.metadata" (dict "c" $c "annotations" $v.annotations) | nindent 2 }}
spec:
  {{- if not $v.autoscaling.enabled }}
  replicas: {{ $v.replicas }}
  {{- end }}
  revisionHistoryLimit: {{ $v.revisionHistoryLimit }}
  {{- with $v.minReadySeconds }}
  minReadySeconds: {{ . }}
  {{- end }}
  strategy:
    type: {{ $v.strategy.type }}
    {{- if and (eq $v.strategy.type "RollingUpdate") $v.strategy.rollingUpdate }}
    rollingUpdate:
      {{- toYaml $v.strategy.rollingUpdate | nindent 6 }}
    {{- end }}
  selector:
    matchLabels:
      {{- include "runvance-service.selectorLabels" $c | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "runvance-service.labels" $c | nindent 8 }}
        {{- if $v.serviceAccount.workloadIdentity.clientId }}
        azure.workload.identity/use: "true"
        {{- end }}
        {{- with $v.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- if or $v.metrics.enabled $v.podAnnotations }}
      annotations:
        {{- if $v.metrics.enabled }}
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ $d.mgmtPort | quote }}
        prometheus.io/path: {{ $v.metrics.path | quote }}
        {{- end }}
        {{- with $v.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- end }}
    spec:
      serviceAccountName: {{ ternary $d.serviceAccountName ($v.serviceAccount.name | default "default") $v.serviceAccount.create }}
      automountServiceAccountToken: {{ $v.serviceAccount.automountToken }}
      # Service links would put e.g. REDIS_PORT=tcp://... into the environment, which Spring binds.
      enableServiceLinks: false
      terminationGracePeriodSeconds: {{ $v.terminationGracePeriodSeconds }}
      {{- with $v.priorityClassName }}
      priorityClassName: {{ . }}
      {{- end }}
      {{- with $v.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- toYaml $v.podSecurityContext | nindent 8 }}
      {{- if $v.topologySpread.enabled }}
      topologySpreadConstraints:
        {{- range $key := list "zone" "hostname" }}
        {{- if index $v.topologySpread $key }}
        - maxSkew: 1
          topologyKey: {{ ternary "topology.kubernetes.io/zone" "kubernetes.io/hostname" (eq $key "zone") }}
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              {{- include "runvance-service.selectorLabels" $c | nindent 14 }}
        {{- end }}
        {{- end }}
      {{- end }}
      {{- with $v.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $v.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $v.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $v.extraInitContainers }}
      initContainers:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: {{ $v.component }}
          image: {{ include "runvance-service.image" $c | quote }}
          imagePullPolicy: {{ $v.image.pullPolicy }}
          {{- with $v.command }}
          command:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with $v.args }}
          args:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          ports:
            - { name: http, containerPort: {{ $d.httpPort }}, protocol: TCP }
            {{- if ne $d.mgmtPortName "http" }}
            - { name: management, containerPort: {{ $d.mgmtPort }}, protocol: TCP }
            {{- end }}
            {{- with $v.extraPorts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with (include "runvance-service.env" $c | trim) }}
          {{- . | nindent 10 }}
          {{- end }}
          {{- with (include "runvance-service.envFrom" $c | trim) }}
          {{- . | nindent 10 }}
          {{- end }}
          {{- range $kind := list "startup" "readiness" "liveness" }}
          {{- $p := index $v.probes $kind }}
          {{- if $p.enabled }}
          {{ $kind }}Probe:
            {{- include "runvance-service.probe" (dict "p" $p "c" $c) | nindent 12 }}
          {{- end }}
          {{- end }}
          resources:
            {{- toYaml $v.resources | nindent 12 }}
          securityContext:
            {{- toYaml $v.securityContext | nindent 12 }}
          {{- if gt (int $v.preStopSleepSeconds) 0 }}
          lifecycle:
            preStop:
              exec: { command: ["sleep", {{ $v.preStopSleepSeconds | quote }}] }
          {{- end }}
          {{- if or $v.tmpVolume.enabled $v.extraVolumeMounts }}
          volumeMounts:
            {{- if $v.tmpVolume.enabled }}
            - { name: tmp, mountPath: /tmp }
            {{- end }}
            {{- with $v.extraVolumeMounts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- end }}
        {{- with $v.extraContainers }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- if or $v.tmpVolume.enabled $v.extraVolumes }}
      volumes:
        {{- if $v.tmpVolume.enabled }}
        - name: tmp
          emptyDir: { sizeLimit: {{ $v.tmpVolume.sizeLimit | quote }} }
        {{- end }}
        {{- with $v.extraVolumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- end }}
{{- end }}
{{- end -}}

{{/* The container's env: what the platform sets (unless env names it too), then env, then extraEnv. */}}
{{- define "runvance-service.env" -}}
{{- $v := .v -}}
{{- $d := $v._d -}}
{{- $platform := list -}}
{{- if $v.spring.enabled -}}
{{- $platform = append $platform (dict "name" "CONFIG_IMPORT" "value" $v.spring.configImport) -}}
{{- with $d.springProfiles -}}
{{- $platform = append $platform (dict "name" "SPRING_PROFILES_ACTIVE" "value" .) -}}
{{- end -}}
{{- with $v.spring.javaToolOptions -}}
{{- $platform = append $platform (dict "name" "JAVA_TOOL_OPTIONS" "value" .) -}}
{{- end -}}
{{- end -}}
{{- if $v.consul.enabled -}}
{{- $platform = append $platform (dict "name" "CONSUL_HOST" "value" $v.consul.host) -}}
{{- $platform = append $platform (dict "name" "CONSUL_PORT" "value" (toString $v.consul.port)) -}}
{{- if ne $v.consul.scheme "http" -}}
{{- $platform = append $platform (dict "name" "SPRING_CLOUD_CONSUL_SCHEME" "value" $v.consul.scheme) -}}
{{- end -}}
{{- if and $v.spring.enabled $v.consul.registerPodIp -}}
{{- $platform = append $platform (dict "name" "SPRING_CLOUD_CONSUL_DISCOVERY_IP_ADDRESS" "valueFrom" (dict "fieldRef" (dict "fieldPath" "status.podIP"))) -}}
{{- end -}}
{{- end -}}
{{- $own := $v.env | default dict -}}
{{- if or $platform $own $v.extraEnv }}
env:
  {{- range $platform }}
  {{- if not (hasKey $own .name) }}
  - {{ toJson . }}
  {{- end }}
  {{- end }}
  {{- range $name, $value := $own }}
  {{- if kindIs "map" $value }}
  - name: {{ $name }}
    valueFrom:
      {{- toYaml $value | nindent 6 }}
  {{- else if not (kindIs "invalid" $value) }}
  - { name: {{ $name }}, value: {{ toString $value | quote }} }
  {{- end }}
  {{- end }}
  {{- with $v.extraEnv }}
  {{- toYaml . | nindent 2 }}
  {{- end }}
{{- end }}
{{- end -}}

{{- define "runvance-service.envFrom" -}}
{{- $v := .v -}}
{{- if or $v._d.hasSecrets $v.secrets.optional $v.extraEnvFrom }}
envFrom:
  {{- if $v._d.hasSecrets }}
  - secretRef: { name: {{ $v._d.secretName }} }
  {{- end }}
  {{- range $group, $_ := $v.secrets.optional }}
  - secretRef: { name: {{ printf "%s-%s" $v.name $group }}, optional: true }
  {{- end }}
  {{- with $v.extraEnvFrom }}
  {{- toYaml . | nindent 2 }}
  {{- end }}
{{- end }}
{{- end -}}

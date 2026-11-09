{{- define "consul-config.labels" -}}
app: consul-config-sync
app.kubernetes.io/name: consul-config
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: job
app.kubernetes.io/part-of: runvance-platform
runvance.com/environment: {{ required "environment is required (values-<env>.yaml)" .Values.environment | quote }}
{{- end -}}

{{/* The files the job needs, at the rendered commit: { ConfigMap key: path under the work dir }. */}}
{{- define "consul-config.files" -}}
{{- $index := dict -}}
{{- range $path, $_ := .Files.Glob "files/config/**" -}}
{{- $_ := set $index (trimPrefix "files/" $path | replace "/" "__" | replace "," "--") (trimPrefix "files/" $path) -}}
{{- end -}}
{{- range list "files/scripts/validate.py" "files/scripts/sync.sh" -}}
{{- if not ($.Files.Get .) -}}{{- fail (printf "consul-config: %s is missing" .) -}}{{- end -}}
{{- $_ := set $index (trimPrefix "files/" . | replace "/" "__") (trimPrefix "files/" .) -}}
{{- end -}}
{{- toJson $index -}}
{{- end -}}

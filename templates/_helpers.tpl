{{/*
Expand the name of the chart.
*/}}
{{- define "sartor.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "sartor.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "sartor.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Namespace to deploy into.
*/}}
{{- define "sartor.namespace" -}}
{{- default .Release.Namespace .Values.namespaceOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Per-component resource names.
*/}}
{{- define "sartor.controller.fullname" -}}
{{- printf "%s-controller" (include "sartor.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "sartor.server.fullname" -}}
{{- printf "%s-server" (include "sartor.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "sartor.ui.fullname" -}}
{{- printf "%s-ui" (include "sartor.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "sartor.labels" -}}
helm.sh/chart: {{ include "sartor.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: sartor
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Component labels: include with (dict "ctx" $ "component" "controller")
*/}}
{{- define "sartor.componentLabels" -}}
{{ include "sartor.labels" .ctx }}
{{ include "sartor.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "sartor.selectorLabels" -}}
app.kubernetes.io/name: sartor-{{ .component }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
{{- end }}

{{- define "sartor.controller.labels" -}}
{{ include "sartor.componentLabels" (dict "ctx" . "component" "controller") }}
{{- end }}

{{- define "sartor.controller.selectorLabels" -}}
{{ include "sartor.selectorLabels" (dict "ctx" . "component" "controller") }}
{{- end }}

{{- define "sartor.server.labels" -}}
{{ include "sartor.componentLabels" (dict "ctx" . "component" "server") }}
{{- end }}

{{- define "sartor.server.selectorLabels" -}}
{{ include "sartor.selectorLabels" (dict "ctx" . "component" "server") }}
{{- end }}

{{- define "sartor.ui.labels" -}}
{{ include "sartor.componentLabels" (dict "ctx" . "component" "ui") }}
{{- end }}

{{- define "sartor.ui.selectorLabels" -}}
{{ include "sartor.selectorLabels" (dict "ctx" . "component" "ui") }}
{{- end }}

{{/*
Image reference: include with (dict "image" .Values.controller.image "ctx" $)
Renders repo:tag, repo@digest or repo:tag@digest.
*/}}
{{- define "sartor.image" -}}
{{- $tag := default .ctx.Chart.AppVersion .image.tag | toString }}
{{- if .image.digest }}
{{- if $tag }}
{{- printf "%s:%s@%s" .image.repository $tag .image.digest }}
{{- else }}
{{- printf "%s@%s" .image.repository .image.digest }}
{{- end }}
{{- else }}
{{- printf "%s:%s" .image.repository $tag }}
{{- end }}
{{- end }}

{{/*
Service account names
*/}}
{{- define "sartor.controller.serviceAccountName" -}}
{{- if .Values.controller.serviceAccount.create }}
{{- default (include "sartor.controller.fullname" .) .Values.controller.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.controller.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "sartor.server.serviceAccountName" -}}
{{- if .Values.server.serviceAccount.create }}
{{- default (include "sartor.server.fullname" .) .Values.server.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.server.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
UI upstream and API key Secret. nginx's runtime resolver skips search domains,
so the default upstream is the fully qualified server Service.
*/}}
{{- define "sartor.ui.apiUrl" -}}
{{- .Values.ui.apiUrl | default (printf "http://%s.%s.svc.%s:%d" (include "sartor.server.fullname" .) (include "sartor.namespace" .) .Values.clusterDomain (int .Values.server.service.port)) }}
{{- end }}

{{- define "sartor.ui.apiKeySecretName" -}}
{{- if .Values.ui.apiKey.existingSecret }}
{{- .Values.ui.apiKey.existingSecret }}
{{- else if .Values.ui.apiKey.value }}
{{- printf "%s-api-key" (include "sartor.ui.fullname" .) }}
{{- end }}
{{- end }}

{{- define "sartor.ui.apiKeySecretKey" -}}
{{- if .Values.ui.apiKey.existingSecret }}
{{- .Values.ui.apiKey.existingSecretKey }}
{{- else }}
{{- "api-key" }}
{{- end }}
{{- end }}

{{/*
Controller arguments. Safety flags are always passed explicitly so the
rendered manifest documents what the controller is allowed to do.
*/}}
{{- define "sartor.controller.args" -}}
{{- $c := .Values.controller -}}
- --leader-elect={{ $c.leaderElection.enabled }}
- --health-probe-bind-address=:{{ $c.health.port }}
{{- if $c.metrics.enabled }}
- --metrics-bind-address=:{{ $c.metrics.port }}
- --metrics-secure={{ $c.metrics.secure }}
{{- else }}
- --metrics-bind-address=0
{{- end }}
- --enable-oom-patching={{ $c.oomPatching.enabled }}
- --enable-git-writeback={{ $c.gitWriteback.enabled }}
- --analysis-interval={{ $c.analysisInterval }}
- --zap-log-level={{ $c.log.level }}
- --zap-encoder={{ $c.log.encoder }}
- --zap-devel={{ $c.log.development }}
{{- with $c.extraArgs }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Validate value combinations that the schema cannot express.
*/}}
{{- define "sartor.validate" -}}
{{- if and (gt (int .Values.controller.replicaCount) 1) (not .Values.controller.leaderElection.enabled) }}
{{- fail "controller.replicaCount > 1 requires controller.leaderElection.enabled=true" }}
{{- end }}
{{- if and .Values.ui.enabled (not .Values.server.enabled) (not .Values.ui.apiUrl) }}
{{- fail "ui.enabled=true requires server.enabled=true (or an explicit ui.apiUrl)" }}
{{- end }}
{{- if and .Values.ui.apiKey.existingSecret .Values.ui.apiKey.value }}
{{- fail "set only one of ui.apiKey.existingSecret and ui.apiKey.value" }}
{{- end }}
{{- $managed := list "leader-elect" "health-probe-bind-address" "metrics-bind-address" "metrics-secure" "enable-oom-patching" "enable-git-writeback" "analysis-interval" "zap-log-level" "zap-encoder" "zap-devel" }}
{{- range .Values.controller.extraArgs }}
{{- $flagName := . | toString | trimPrefix "-" | trimPrefix "-" | splitList "=" | first }}
{{- if has $flagName $managed }}
{{- fail (printf "controller.extraArgs must not set --%s; use the dedicated chart value instead" $flagName) }}
{{- end }}
{{- end }}
{{- $m := .Values.controller.metrics }}
{{- if and (or $m.serviceMonitor.enabled $m.vmServiceScrape.enabled) (not (and .Values.controller.enabled $m.enabled)) }}
{{- fail "controller.metrics.serviceMonitor / vmServiceScrape require controller.enabled=true and controller.metrics.enabled=true" }}
{{- end }}
{{- end }}

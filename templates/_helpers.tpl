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
Namespace to use
*/}}
{{- define "sartor.namespace" -}}
{{- .Values.global.namespace | default .Release.Namespace }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "sartor.labels" -}}
helm.sh/chart: {{ include "sartor.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: sartor
{{- end }}

{{/*
Controller labels
*/}}
{{- define "sartor.controller.labels" -}}
{{ include "sartor.labels" . }}
app.kubernetes.io/name: sartor-controller
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: controller
{{- end }}

{{/*
Controller selector labels
*/}}
{{- define "sartor.controller.selectorLabels" -}}
app.kubernetes.io/name: sartor-controller
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Server labels
*/}}
{{- define "sartor.server.labels" -}}
{{ include "sartor.labels" . }}
app.kubernetes.io/name: sartor-server
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: server
{{- end }}

{{/*
Server selector labels
*/}}
{{- define "sartor.server.selectorLabels" -}}
app.kubernetes.io/name: sartor-server
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
UI labels
*/}}
{{- define "sartor.ui.labels" -}}
{{ include "sartor.labels" . }}
app.kubernetes.io/name: sartor-ui
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: ui
{{- end }}

{{/*
UI selector labels
*/}}
{{- define "sartor.ui.selectorLabels" -}}
app.kubernetes.io/name: sartor-ui
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Controller image
*/}}
{{- define "sartor.controller.image" -}}
{{- $tag := .Values.controller.image.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.controller.image.repository $tag }}
{{- end }}

{{/*
Server image
*/}}
{{- define "sartor.server.image" -}}
{{- $tag := .Values.server.image.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.server.image.repository $tag }}
{{- end }}

{{/*
UI image
*/}}
{{- define "sartor.ui.image" -}}
{{- $tag := .Values.ui.image.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.ui.image.repository $tag }}
{{- end }}

{{/*
Controller service account name
*/}}
{{- define "sartor.controller.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- .Values.controller.serviceAccountName | default "sartor-controller" }}
{{- else }}
{{- .Values.controller.serviceAccountName | default "default" }}
{{- end }}
{{- end }}

{{/*
Server service account name
*/}}
{{- define "sartor.server.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- .Values.server.serviceAccountName | default "sartor-server" }}
{{- else }}
{{- .Values.server.serviceAccountName | default "default" }}
{{- end }}
{{- end }}

{{/*
Server internal URL for UI
*/}}
{{- define "sartor.server.internalUrl" -}}
{{- $namespace := include "sartor.namespace" . }}
{{- printf "http://sartor-server.%s.svc.cluster.local:%d" $namespace (int .Values.server.service.port) }}
{{- end }}

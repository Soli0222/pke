{{/*
Expand the name of the chart.
*/}}
{{- define "mc-mirror-cronjob.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "mc-mirror-cronjob.fullname" -}}
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
Create chart labels
*/}}
{{- define "mc-mirror-cronjob.labels" -}}
helm.sh/chart: {{ include "mc-mirror-cronjob.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "mc-mirror-cronjob.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "mc-mirror-cronjob.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mc-mirror-cronjob.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "mc-mirror-cronjob.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "mc-mirror-cronjob.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create the name of the secret to use for mc configuration
*/}}
{{- define "mc-mirror-cronjob.secretName" -}}
{{- coalesce .Values.externalSecret.name (printf "%s-secret" (include "mc-mirror-cronjob.fullname" .)) -}}
{{- end }}

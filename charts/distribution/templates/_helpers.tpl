{{/*
Expand the name of the chart.
*/}}
{{- define "distribution.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "distribution.fullname" -}}
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
{{- define "distribution.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "distribution.labels" -}}
helm.sh/chart: {{ include "distribution.chart" . }}
{{ include "distribution.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "distribution.selectorLabels" -}}
app.kubernetes.io/name: {{ include "distribution.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "distribution.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "distribution.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
PVC name
*/}}
{{- define "distribution.pvcName" -}}
{{- if .Values.persistence.existingClaim }}
{{- .Values.persistence.existingClaim }}
{{- else }}
{{- printf "%s-data" (include "distribution.fullname" .) }}
{{- end }}
{{- end }}

{{/*
ConfigMap name
*/}}
{{- define "distribution.configMapName" -}}
{{- printf "%s-config" (include "distribution.fullname" .) }}
{{- end }}

{{/*
Secret name for HTTP secret
*/}}
{{- define "distribution.httpSecretName" -}}
{{- printf "%s-http-secret" (include "distribution.fullname" .) }}
{{- end }}

{{/*
Secret name for TLS
*/}}
{{- define "distribution.tlsSecretName" -}}
{{- if .Values.tls.existingSecret }}
{{- .Values.tls.existingSecret }}
{{- else }}
{{- printf "%s-tls" (include "distribution.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Secret name for htpasswd
*/}}
{{- define "distribution.htpasswdSecretName" -}}
{{- if .Values.htpasswd.existingSecret }}
{{- .Values.htpasswd.existingSecret }}
{{- else }}
{{- printf "%s-htpasswd" (include "distribution.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Secret name for credentials (S3, Azure, GCS, Redis, Proxy)
*/}}
{{- define "distribution.credentialsSecretName" -}}
{{- if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- printf "%s-credentials" (include "distribution.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Generate HTTP secret if not provided
*/}}
{{- define "distribution.httpSecret" -}}
{{- if .Values.config.http.secret }}
{{- .Values.config.http.secret }}
{{- else }}
{{- randAlphaNum 32 }}
{{- end }}
{{- end }}

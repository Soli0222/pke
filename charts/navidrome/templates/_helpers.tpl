{{/*
Expand the name of the chart.
*/}}
{{- define "navidrome.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "navidrome.fullname" -}}
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
{{- define "navidrome.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "navidrome.labels" -}}
helm.sh/chart: {{ include "navidrome.chart" . }}
{{ include "navidrome.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "navidrome.selectorLabels" -}}
app.kubernetes.io/name: {{ include "navidrome.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "navidrome.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "navidrome.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Data PVC name - maintain existing naming for backward compatibility
*/}}
{{- define "navidrome.dataPvcName" -}}
{{- if .Values.persistence.data.existingClaim }}
{{- .Values.persistence.data.existingClaim }}
{{- else }}
{{- printf "%s-data-pvc" (include "navidrome.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Music PVC name - maintain existing naming for backward compatibility  
*/}}
{{- define "navidrome.musicPvcName" -}}
{{- if .Values.persistence.music.existingClaim }}
{{- .Values.persistence.music.existingClaim }}
{{- else }}
{{- printf "%s-music-pvc" (include "navidrome.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Music PV name - maintain existing naming for backward compatibility
*/}}
{{- define "navidrome.musicPvName" -}}
{{- if .Values.persistence.music.existingVolume }}
{{- .Values.persistence.music.existingVolume }}
{{- else }}
{{- printf "%s-music-pv" (include "navidrome.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Ingress block service name
*/}}
{{- define "navidrome.ingressBlockServiceName" -}}
{{- printf "%s-ingress-block" (include "navidrome.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

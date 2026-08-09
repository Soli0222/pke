{{/*
Expand the name of the chart.
*/}}
{{- define "misskey.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "misskey.fullname" -}}
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
{{- define "misskey.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "misskey.labels" -}}
helm.sh/chart: {{ include "misskey.chart" . }}
{{ include "misskey.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "misskey.selectorLabels" -}}
app.kubernetes.io/name: {{ include "misskey.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "misskey.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "misskey.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
PostgreSQL host
*/}}
{{- define "misskey.postgresql.host" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-postgresql" (include "misskey.fullname" .) }}
{{- else }}
{{- .Values.externalPostgresql.host }}
{{- end }}
{{- end }}

{{/*
PostgreSQL port
*/}}
{{- define "misskey.postgresql.port" -}}
{{- if .Values.postgresql.enabled }}
{{- .Values.postgresql.service.port }}
{{- else }}
{{- .Values.externalPostgresql.port }}
{{- end }}
{{- end }}

{{/*
PostgreSQL database name
*/}}
{{- define "misskey.postgresql.database" -}}
{{- if .Values.postgresql.enabled }}
{{- .Values.postgresql.database }}
{{- else }}
{{- .Values.externalPostgresql.database }}
{{- end }}
{{- end }}

{{/*
PostgreSQL username
*/}}
{{- define "misskey.postgresql.username" -}}
{{- if .Values.postgresql.enabled }}
{{- .Values.postgresql.username }}
{{- else }}
{{- .Values.externalPostgresql.username }}
{{- end }}
{{- end }}

{{/*
Redis host
*/}}
{{- define "misskey.redis.host" -}}
{{- if .Values.redis.enabled }}
{{- printf "%s-redis" (include "misskey.fullname" .) }}
{{- else }}
{{- .Values.externalRedis.host }}
{{- end }}
{{- end }}

{{/*
Redis port
*/}}
{{- define "misskey.redis.port" -}}
{{- if .Values.redis.enabled }}
{{- .Values.redis.service.port }}
{{- else }}
{{- .Values.externalRedis.port }}
{{- end }}
{{- end }}

{{/*
Secret name for PostgreSQL
*/}}
{{- define "misskey.postgresql.secretName" -}}
{{- if .Values.postgresql.enabled }}
{{- if .Values.postgresql.existingSecret }}
{{- .Values.postgresql.existingSecret }}
{{- else }}
{{- printf "%s-postgresql" (include "misskey.fullname" .) }}
{{- end }}
{{- else }}
{{- if .Values.externalPostgresql.existingSecret }}
{{- .Values.externalPostgresql.existingSecret }}
{{- else }}
{{- printf "%s-external-postgresql" (include "misskey.fullname" .) }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Secret key for PostgreSQL password
*/}}
{{- define "misskey.postgresql.secretKey" -}}
{{- if .Values.postgresql.enabled }}
{{- if .Values.postgresql.existingSecret }}
{{- default "password" .Values.postgresql.existingSecretKey }}
{{- else }}
{{- "password" }}
{{- end }}
{{- else }}
{{- if .Values.externalPostgresql.existingSecret }}
{{- default "password" .Values.externalPostgresql.existingSecretKey }}
{{- else }}
{{- "password" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Secret name for Redis
*/}}
{{- define "misskey.redis.secretName" -}}
{{- if .Values.redis.enabled }}
{{- if .Values.redis.existingSecret }}
{{- .Values.redis.existingSecret }}
{{- else }}
{{- printf "%s-redis" (include "misskey.fullname" .) }}
{{- end }}
{{- else }}
{{- if .Values.externalRedis.existingSecret }}
{{- .Values.externalRedis.existingSecret }}
{{- else }}
{{- printf "%s-external-redis" (include "misskey.fullname" .) }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Secret key for Redis password
*/}}
{{- define "misskey.redis.secretKey" -}}
{{- if .Values.redis.enabled }}
{{- if .Values.redis.existingSecret }}
{{- default "password" .Values.redis.existingSecretKey }}
{{- else }}
{{- "password" }}
{{- end }}
{{- else }}
{{- if .Values.externalRedis.existingSecret }}
{{- default "password" .Values.externalRedis.existingSecretKey }}
{{- else }}
{{- "password" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Get password for Redis Pubsub
*/}}
{{- define "misskey.redisForPubsub.password" -}}
{{- .Values.redisForPubsub.password }}
{{- end }}

{{/*
Get password for Redis JobQueue
*/}}
{{- define "misskey.redisForJobQueue.password" -}}
{{- .Values.redisForJobQueue.password }}
{{- end }}

{{/*
Get password for Redis Timelines
*/}}
{{- define "misskey.redisForTimelines.password" -}}
{{- .Values.redisForTimelines.password }}
{{- end }}

{{/*
Get password for Redis Reactions
*/}}
{{- define "misskey.redisForReactions.password" -}}
{{- .Values.redisForReactions.password }}
{{- end }}

{{/*
Get PostgreSQL password (for reference, password is passed via DATABASE_URL env var)
*/}}
{{- define "misskey.postgresql.password" -}}
{{- if .Values.postgresql.enabled }}
{{- .Values.postgresql.password }}
{{- else }}
{{- .Values.externalPostgresql.password }}
{{- end }}
{{- end }}

{{/*
Get Redis password
*/}}
{{- define "misskey.redis.password" -}}
{{- if .Values.redis.enabled }}
{{- .Values.redis.password }}
{{- else }}
{{- .Values.externalRedis.password }}
{{- end }}
{{- end }}

{{/*
Node.js diagnostic report options for the web process.
*/}}
{{- define "misskey.nodeDiagnosticReportOptions" -}}
{{- $report := .Values.web.diagnosticReport -}}
{{- $options := list -}}
{{- if $report.reportOnFatalError }}
{{- $options = append $options "--report-on-fatalerror" -}}
{{- end -}}
{{- if $report.reportOnUncaughtException }}
{{- $options = append $options "--report-uncaught-exception" -}}
{{- end -}}
{{- if $report.reportOnSignal }}
{{- $options = append $options "--report-on-signal" -}}
{{- end -}}
{{- if and $report.reportOnSignal $report.signal }}
{{- $options = append $options (printf "--report-signal=%s" $report.signal) -}}
{{- end -}}
{{- $options = append $options (printf "--report-directory=%s" $report.directory) -}}
{{- if $report.compact }}
{{- $options = append $options "--report-compact" -}}
{{- end -}}
{{- join " " $options -}}
{{- end }}

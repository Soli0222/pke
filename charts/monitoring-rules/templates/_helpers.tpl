{{/* Cluster scope is injected by Alloy, including absent expressions and output labels. */}}
{{- define "monitoring-rules.group" -}}
{{- $root := .root -}}
{{- $group := index $root.Values.groups .name -}}
{{- if $group.enabled -}}
{{- $rules := list -}}
{{- range $rule := ($root.Files.Get (printf "rules/%s.yaml" $.name) | fromYaml).rules -}}
{{- $config := index $group.rules $rule.alert -}}
{{- $eligible := true -}}
{{- if eq ($rule.requires | default "") "hosts" -}}{{- $eligible = not (empty $root.Values.hosts) -}}{{- end -}}
{{- if eq ($rule.requires | default "") "databases" -}}{{- $eligible = not (empty $root.Values.databases) -}}{{- end -}}
{{- if and $config.enabled $eligible -}}
{{- $labels := mergeOverwrite (dict "severity" $config.severity "alert_family" ($rule.family | default $rule.alert)) ($rule.labels | default dict) -}}
{{- $links := $root.Files.Get "dashboard-links.yaml" | fromYaml -}}
{{- $link := mergeOverwrite (deepCopy (index $links.groups $.name)) (index $links.rules $rule.alert | default dict) -}}
{{- $dashboardURL := printf "%s/d/%s?%s&from=now-6h&to=now" (trimSuffix "/" $root.Values.dashboardBaseURL) $link.uid $link.query -}}
{{- $annotations := mergeOverwrite (dict "summary" $rule.summary "description" $rule.description "runbook_url" (printf "%s#%s" $root.Values.runbookBaseURL $.name) "dashboard_url" $dashboardURL "panel_url" (printf "%s&viewPanel=%v" $dashboardURL $link.panel)) (deepCopy $root.Values.additionalAnnotations) ($config.annotations | default dict) -}}
{{- $rules = append $rules (dict "alert" $rule.alert "expr" (tpl $rule.expr $root | trim) "for" $config.for "labels" $labels "annotations" $annotations) -}}
{{- end -}}
{{- end -}}
{{- if $rules }}
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: {{ printf "%s-%s" $root.Release.Name .name | trunc 63 | trimSuffix "-" }}
  labels:
    app.kubernetes.io/name: monitoring-rules
    app.kubernetes.io/instance: {{ $root.Release.Name }}
spec:
  groups:
    - name: pke.{{ .name }}
      interval: {{ $root.Values.interval }}
      rules:
{{ toYaml $rules | indent 8 }}
{{- end -}}
{{- end -}}
{{- end -}}

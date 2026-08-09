# mimir

![Version: 0.1.5](https://img.shields.io/badge/Version-0.1.5-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 3.1.2](https://img.shields.io/badge/AppVersion-3.1.2-informational?style=flat-square)

A Helm chart for Grafana Mimir running in monolithic mode

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Affinity |
| env | list | `[]` | Environment variables added to the Mimir container |
| envFrom | list | `[]` | envFrom entries added to the Mimir container. Use this to expose object storage credentials such as AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from an existing Secret. |
| extraArgs | object | `{}` | Extra command-line arguments appended as `-key=value` |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the Mimir container |
| extraVolumes | list | `[]` | Additional volumes added to the Pod |
| fullnameOverride | string | `""` | Override the full name of the chart |
| image | object | `{"pullPolicy":"IfNotPresent","repository":"grafana/mimir","tag":""}` | Image configuration |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| image.repository | string | `"grafana/mimir"` | Grafana Mimir image repository |
| image.tag | string | `""` | Grafana Mimir image tag. Defaults to the chart appVersion when empty. |
| imagePullSecrets | list | `[]` | Image pull secrets |
| ingresses | list | `[]` | Ingress definitions. Because monolithic Mimir exposes a single HTTP endpoint, each ingress backend always points to the main Service HTTP port. |
| livenessProbe | object | `{"failureThreshold":5,"httpGet":{"path":"/ready","port":"http"},"initialDelaySeconds":45,"periodSeconds":15,"timeoutSeconds":5}` | Liveness probe configuration |
| memberlistService | object | `{"annotations":{},"port":7946}` | Headless memberlist Service configuration |
| memberlistService.annotations | object | `{}` | Memberlist Service annotations |
| memberlistService.port | int | `7946` | Memberlist port exposed by the headless Service and container |
| mimir | object | `{"config":"target: all\nmultitenancy_enabled: false\n\ncommon:\n  storage:\n    backend: s3\n    s3:\n      endpoint: s3.example.com\n      region: us-east-1\n      access_key_id: ${AWS_ACCESS_KEY_ID}\n      secret_access_key: ${AWS_SECRET_ACCESS_KEY}\n      insecure: false\n\nblocks_storage:\n  s3:\n    bucket_name: mimir-blocks\n  bucket_store:\n    sync_dir: /data/tsdb-sync\n  tsdb:\n    dir: /data/tsdb\n    head_compaction_interval: 15m\n    wal_replay_concurrency: 3\n\ncompactor:\n  data_dir: /data/compactor\n  sharding_ring:\n    kvstore:\n      store: memberlist\n\ndistributor:\n  ring:\n    kvstore:\n      store: memberlist\n\ningester:\n  ring:\n    replication_factor: {{ .Values.replicaCount }}\n    kvstore:\n      store: memberlist\n\nstore_gateway:\n  sharding_ring:\n    replication_factor: {{ .Values.replicaCount }}\n    kvstore:\n      store: memberlist\n\nlimits:\n  compactor_blocks_retention_period: 60d\n  max_global_series_per_user: 1000000\n  ingestion_rate: 50000\n  ingestion_burst_size: 1000000\n  out_of_order_time_window: 5m\n\nmemberlist:\n  bind_port: {{ .Values.memberlistService.port }}\n  abort_if_cluster_join_fails: false\n  join_members:\n    - dnssrv+{{ include \"mimir.memberlistServiceName\" . }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.memberlistService.port }}\n\nserver:\n  http_listen_port: {{ .Values.service.httpPort }}\n  grpc_listen_port: {{ .Values.service.grpcPort }}\n  log_level: info\n","structuredConfig":{}}` | Mimir configuration. This string is rendered with `tpl`, then merged with `mimir.structuredConfig`. |
| mimir.config | string | `"target: all\nmultitenancy_enabled: false\n\ncommon:\n  storage:\n    backend: s3\n    s3:\n      endpoint: s3.example.com\n      region: us-east-1\n      access_key_id: ${AWS_ACCESS_KEY_ID}\n      secret_access_key: ${AWS_SECRET_ACCESS_KEY}\n      insecure: false\n\nblocks_storage:\n  s3:\n    bucket_name: mimir-blocks\n  bucket_store:\n    sync_dir: /data/tsdb-sync\n  tsdb:\n    dir: /data/tsdb\n    head_compaction_interval: 15m\n    wal_replay_concurrency: 3\n\ncompactor:\n  data_dir: /data/compactor\n  sharding_ring:\n    kvstore:\n      store: memberlist\n\ndistributor:\n  ring:\n    kvstore:\n      store: memberlist\n\ningester:\n  ring:\n    replication_factor: {{ .Values.replicaCount }}\n    kvstore:\n      store: memberlist\n\nstore_gateway:\n  sharding_ring:\n    replication_factor: {{ .Values.replicaCount }}\n    kvstore:\n      store: memberlist\n\nlimits:\n  compactor_blocks_retention_period: 60d\n  max_global_series_per_user: 1000000\n  ingestion_rate: 50000\n  ingestion_burst_size: 1000000\n  out_of_order_time_window: 5m\n\nmemberlist:\n  bind_port: {{ .Values.memberlistService.port }}\n  abort_if_cluster_join_fails: false\n  join_members:\n    - dnssrv+{{ include \"mimir.memberlistServiceName\" . }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.memberlistService.port }}\n\nserver:\n  http_listen_port: {{ .Values.service.httpPort }}\n  grpc_listen_port: {{ .Values.service.grpcPort }}\n  log_level: info\n"` | Base Mimir config rendered into `mimir.yaml`. Prefer official config keys here. |
| mimir.structuredConfig | object | `{}` | Structured overrides merged on top of `mimir.config`. Use this to add official config blocks such as `ruler_storage` or `alertmanager_storage` without copying the whole config. |
| nameOverride | string | `""` | Override the name of the chart |
| nodeSelector | object | `{}` | Node selector |
| persistence | object | `{"accessModes":["ReadWriteOnce"],"annotations":{},"enabled":true,"existingClaim":"","size":"10Gi","storageClass":""}` | Persistent storage configuration for WAL, TSDB state, and compactor data |
| persistence.accessModes | list | `["ReadWriteOnce"]` | PVC access modes |
| persistence.annotations | object | `{}` | PVC annotations |
| persistence.enabled | bool | `true` | Enable persistent storage. Disable to use emptyDir. |
| persistence.existingClaim | string | `""` | Use an existing PVC instead of creating a per-pod volume claim. Best suited for single replica deployments. |
| persistence.size | string | `"10Gi"` | PVC size |
| persistence.storageClass | string | `""` | PVC storage class |
| podAnnotations | object | `{}` | Pod annotations |
| podLabels | object | `{}` | Pod labels |
| podManagementPolicy | string | `"OrderedReady"` | Pod management policy for the StatefulSet |
| podSecurityContext | object | `{}` | Pod security context |
| readinessProbe | object | `{"failureThreshold":6,"httpGet":{"path":"/ready","port":"http"},"initialDelaySeconds":15,"periodSeconds":10,"timeoutSeconds":5}` | Readiness probe configuration |
| replicaCount | int | `1` | Number of Mimir replicas. When using classic monolithic mode, align ring replication factors with this value. |
| resources | object | `{"limits":{"memory":"2Gi"},"requests":{"cpu":"200m","memory":"512Mi"}}` | Resource requests and limits |
| securityContext | object | `{}` | Container security context |
| service | object | `{"annotations":{},"grpcPort":9095,"httpPort":8080,"type":"ClusterIP"}` | Service configuration |
| service.annotations | object | `{}` | Service annotations |
| service.grpcPort | int | `9095` | gRPC port exposed by the main Service and container |
| service.httpPort | int | `8080` | HTTP port exposed by the main Service and container |
| service.type | string | `"ClusterIP"` | Service type |
| serviceAccount | object | `{"annotations":{},"create":false,"name":""}` | Service account configuration |
| serviceAccount.annotations | object | `{}` | Annotations to add to the service account |
| serviceAccount.create | bool | `false` | Specifies whether a service account should be created |
| serviceAccount.name | string | `""` | The name of the service account to use |
| serviceMonitor | object | `{"annotations":{},"enabled":false,"interval":"30s","jobLabel":"","labels":{},"metricRelabelings":[],"namespace":"","path":"/metrics","relabelings":[],"scrapeTimeout":"10s","selector":{}}` | ServiceMonitor configuration |
| serviceMonitor.annotations | object | `{}` | Annotations added to the ServiceMonitor |
| serviceMonitor.enabled | bool | `false` | Enable creation of a ServiceMonitor |
| serviceMonitor.interval | string | `"30s"` | Scrape interval |
| serviceMonitor.jobLabel | string | `""` | Optional jobLabel |
| serviceMonitor.labels | object | `{}` | Labels added to the ServiceMonitor |
| serviceMonitor.metricRelabelings | list | `[]` | Metric relabelings for the ServiceMonitor endpoint |
| serviceMonitor.namespace | string | `""` | Namespace for the ServiceMonitor. Defaults to the release namespace when empty. |
| serviceMonitor.path | string | `"/metrics"` | Metrics path |
| serviceMonitor.relabelings | list | `[]` | Relabelings for the ServiceMonitor endpoint |
| serviceMonitor.scrapeTimeout | string | `"10s"` | Scrape timeout |
| serviceMonitor.selector | object | `{}` | Extra matchLabels added to the ServiceMonitor selector |
| startupProbe | object | `{"failureThreshold":60,"httpGet":{"path":"/ready","port":"http"},"periodSeconds":10,"timeoutSeconds":5}` | Startup probe configuration |
| terminationGracePeriodSeconds | int | `180` | Termination grace period in seconds |
| tolerations | list | `[]` | Tolerations |
| topologySpreadConstraints | list | `[]` | Topology spread constraints |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)

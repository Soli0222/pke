# distribution

![Version: 0.1.3](https://img.shields.io/badge/Version-0.1.3-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 3.1.1](https://img.shields.io/badge/AppVersion-3.1.1-informational?style=flat-square)

A Helm chart for Distribution Registry - A stateless, highly scalable container image registry

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Affinity |
| config | object | `{"auth":{},"enabled":true,"health":{"storagedriver":{"enabled":true,"interval":"10s","threshold":3}},"http":{"addr":":5000","debug":{"addr":":5001","prometheus":{"enabled":true,"path":"/metrics"}},"draintimeout":"60s","h2c":{"enabled":false},"headers":{"X-Content-Type-Options":["nosniff"]},"http2":{"disabled":false},"relativeurls":false,"secret":""},"log":{"accesslog":{"disabled":false},"formatter":"text","level":"info"},"notifications":{},"proxy":{},"redis":{},"storage":{"azure":{},"cache":{"blobdescriptor":"inmemory"},"delete":{"enabled":false},"filesystem":{"rootdirectory":"/var/lib/registry"},"gcs":{},"maintenance":{"uploadpurging":{"age":"168h","dryrun":false,"enabled":true,"interval":"24h"}},"redirect":{"disable":false},"s3":{}},"validation":{"disabled":false},"version":"0.1"}` | Registry configuration. See https://distribution.github.io/distribution/about/configuration/ |
| config.auth | object | `{}` | Authentication configuration. Only one auth method can be configured at a time |
| config.enabled | bool | `true` | Enable custom configuration (if false, uses default registry config) |
| config.health | object | `{"storagedriver":{"enabled":true,"interval":"10s","threshold":3}}` | Health check configuration |
| config.health.storagedriver | object | `{"enabled":true,"interval":"10s","threshold":3}` | Storage driver health check |
| config.http | object | `{"addr":":5000","debug":{"addr":":5001","prometheus":{"enabled":true,"path":"/metrics"}},"draintimeout":"60s","h2c":{"enabled":false},"headers":{"X-Content-Type-Options":["nosniff"]},"http2":{"disabled":false},"relativeurls":false,"secret":""}` | HTTP server configuration |
| config.http.addr | string | `":5000"` | HTTP listen address |
| config.http.debug | object | `{"addr":":5001","prometheus":{"enabled":true,"path":"/metrics"}}` | Debug server configuration (exposes /debug/health and /metrics) |
| config.http.debug.addr | string | `":5001"` | Debug server listen address |
| config.http.debug.prometheus | object | `{"enabled":true,"path":"/metrics"}` | Prometheus metrics configuration |
| config.http.debug.prometheus.enabled | bool | `true` | Enable Prometheus metrics |
| config.http.debug.prometheus.path | string | `"/metrics"` | Metrics path |
| config.http.draintimeout | string | `"60s"` | Time to wait for HTTP connections to drain on shutdown |
| config.http.h2c | object | `{"enabled":false}` | H2C (HTTP/2 Cleartext) configuration |
| config.http.h2c.enabled | bool | `false` | Enable H2C |
| config.http.headers | object | `{"X-Content-Type-Options":["nosniff"]}` | HTTP response headers |
| config.http.http2 | object | `{"disabled":false}` | HTTP/2 configuration |
| config.http.http2.disabled | bool | `false` | Disable HTTP/2 |
| config.http.relativeurls | bool | `false` | Return relative URLs in Location headers |
| config.http.secret | string | `""` | HTTP secret for signing state. Auto-generated if not set |
| config.log | object | `{"accesslog":{"disabled":false},"formatter":"text","level":"info"}` | Logging configuration |
| config.log.accesslog | object | `{"disabled":false}` | Access log configuration |
| config.log.accesslog.disabled | bool | `false` | Disable access logging |
| config.log.formatter | string | `"text"` | Log formatter: text, json, logstash |
| config.log.level | string | `"info"` | Log level: error, warn, info, debug |
| config.notifications | object | `{}` | Notifications configuration for webhooks |
| config.proxy | object | `{}` | Proxy configuration for pull-through cache. Credentials should be set in secrets.proxy |
| config.redis | object | `{}` | Redis configuration for caching. Password should be set in secrets.redis |
| config.storage | object | `{"azure":{},"cache":{"blobdescriptor":"inmemory"},"delete":{"enabled":false},"filesystem":{"rootdirectory":"/var/lib/registry"},"gcs":{},"maintenance":{"uploadpurging":{"age":"168h","dryrun":false,"enabled":true,"interval":"24h"}},"redirect":{"disable":false},"s3":{}}` | Only one storage backend should be configured at a time. S3/Azure/GCS takes priority over filesystem. |
| config.storage.azure | object | `{}` | Azure Blob storage configuration. Credentials should be set in secrets.azure Ref: https://distribution.github.io/distribution/storage-drivers/azure/ |
| config.storage.cache | object | `{"blobdescriptor":"inmemory"}` | Cache configuration for layer metadata |
| config.storage.cache.blobdescriptor | string | `"inmemory"` | Cache type: inmemory or redis |
| config.storage.delete | object | `{"enabled":false}` | Enable deletion of image blobs and manifests by digest |
| config.storage.filesystem | object | enabled by default | Filesystem storage configuration (default backend) Ref: https://distribution.github.io/distribution/storage-drivers/filesystem/ |
| config.storage.filesystem.rootdirectory | string | `"/var/lib/registry"` | Root directory for registry data (default: /var/lib/registry) |
| config.storage.gcs | object | `{}` | Google Cloud Storage configuration. Credentials should be set in secrets.gcs Ref: https://distribution.github.io/distribution/storage-drivers/gcs/ |
| config.storage.maintenance | object | `{"uploadpurging":{"age":"168h","dryrun":false,"enabled":true,"interval":"24h"}}` | Maintenance configuration |
| config.storage.maintenance.uploadpurging | object | `{"age":"168h","dryrun":false,"enabled":true,"interval":"24h"}` | Upload purging configuration to remove orphaned files |
| config.storage.redirect | object | `{"disable":false}` | Redirect configuration for storage backends that support it |
| config.storage.redirect.disable | bool | `false` | Disable redirects to serve all data through the registry |
| config.storage.s3 | object | `{}` | S3 storage configuration. Credentials should be set in secrets.s3 Ref: https://distribution.github.io/distribution/storage-drivers/s3/ |
| config.validation | object | `{"disabled":false}` | Validation configuration |
| config.validation.disabled | bool | `false` | Disable validation |
| config.version | string | `"0.1"` | Configuration version (required) |
| containerPort | int | `5000` | Container port for the registry |
| debugPort | int | `5001` | Debug server port (for metrics and health checks) |
| env | list | `[]` | Extra environment variables |
| extraVolumeMounts | list | `[]` | Extra volume mounts |
| extraVolumes | list | `[]` | Extra volumes |
| fullnameOverride | string | `""` | Override the full name of the chart |
| htpasswd | object | `{"credentials":"","enabled":false,"existingSecret":""}` | htpasswd authentication configuration |
| htpasswd.credentials | string | `""` | htpasswd credentials in bcrypt format (username:bcrypt_hashed_password) |
| htpasswd.enabled | bool | `false` | Enable htpasswd authentication |
| htpasswd.existingSecret | string | `""` | Use existing secret containing htpasswd file |
| image | object | `{"pullPolicy":"IfNotPresent","repository":"registry","tag":""}` | Image configuration |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| image.repository | string | `"registry"` | Registry image repository |
| image.tag | string | `""` | Registry image tag |
| imagePullSecrets | list | `[]` | Image pull secrets |
| ingress | object | `{"annotations":{},"className":"","enabled":false,"hosts":[{"host":"registry.local","paths":[{"path":"/","pathType":"Prefix"}]}],"tls":[]}` | Ingress configuration |
| ingress.annotations | object | `{}` | Ingress annotations |
| ingress.className | string | `""` | Ingress class name |
| ingress.enabled | bool | `false` | Enable ingress |
| ingress.hosts | list | `[{"host":"registry.local","paths":[{"path":"/","pathType":"Prefix"}]}]` | Ingress hosts |
| ingress.tls | list | `[]` | Ingress TLS configuration |
| livenessProbe | object | `{"failureThreshold":3,"httpGet":{"path":"/","port":"http"},"initialDelaySeconds":10,"periodSeconds":10,"timeoutSeconds":5}` | Liveness probe configuration |
| nameOverride | string | `""` | Override the name of the chart |
| nodeSelector | object | `{}` | Node selector |
| otelTracesExporter | string | `"none"` | OpenTelemetry traces exporter. Set to "none" to disable traces, or specify your trace collector endpoint |
| persistence | object | `{"accessModes":["ReadWriteOnce"],"enabled":true,"existingClaim":"","size":"10Gi","storageClass":""}` | Persistence configuration |
| persistence.accessModes | list | `["ReadWriteOnce"]` | Access modes |
| persistence.enabled | bool | `true` | Enable persistence |
| persistence.existingClaim | string | `""` | Use existing PVC (if specified, no new PVC will be created) |
| persistence.size | string | `"10Gi"` | Storage size |
| persistence.storageClass | string | `""` | Storage class for the PVC |
| podAnnotations | object | `{}` | Pod annotations |
| podLabels | object | `{}` | Pod labels |
| podSecurityContext | object | `{}` | Pod security context |
| readinessProbe | object | `{"failureThreshold":3,"httpGet":{"path":"/","port":"http"},"initialDelaySeconds":5,"periodSeconds":10,"timeoutSeconds":5}` | Readiness probe configuration |
| replicaCount | int | `1` | Number of replicas |
| resources | object | `{}` | Resource limits and requests |
| secrets | object | `{"azure":{"accountKey":""},"existingSecret":"","gcs":{"keyfile":""},"proxy":{"password":"","username":""},"redis":{"password":""},"s3":{"accessKey":"","secretKey":""}}` | Secrets configuration for storage backends and other services. Values are stored in a Kubernetes Secret and injected as environment variables |
| secrets.azure | object | `{"accountKey":""}` | Azure storage credentials |
| secrets.azure.accountKey | string | `""` | Azure storage account key (injected as REGISTRY_STORAGE_AZURE_ACCOUNTKEY) |
| secrets.existingSecret | string | `""` | Use existing secret for credentials. If set, other secret values are ignored. The existing secret should contain the following keys: s3-access-key, s3-secret-key, azure-account-key, gcs-keyfile, redis-password, proxy-username, proxy-password |
| secrets.gcs | object | `{"keyfile":""}` | GCS storage credentials |
| secrets.gcs.keyfile | string | `""` | GCS service account JSON key file content (mounted at /etc/distribution/gcs/keyfile.json) |
| secrets.proxy | object | `{"password":"","username":""}` | Proxy credentials for pull-through cache with private upstream |
| secrets.proxy.password | string | `""` | Proxy upstream password (injected as REGISTRY_PROXY_PASSWORD) |
| secrets.proxy.username | string | `""` | Proxy upstream username (injected as REGISTRY_PROXY_USERNAME) |
| secrets.redis | object | `{"password":""}` | Redis credentials |
| secrets.redis.password | string | `""` | Redis password (injected as REGISTRY_REDIS_PASSWORD) |
| secrets.s3 | object | `{"accessKey":"","secretKey":""}` | S3 storage credentials |
| secrets.s3.accessKey | string | `""` | S3 access key (injected as REGISTRY_STORAGE_S3_ACCESSKEY) |
| secrets.s3.secretKey | string | `""` | S3 secret key (injected as REGISTRY_STORAGE_S3_SECRETKEY) |
| securityContext | object | `{}` | Container security context |
| service | object | `{"port":5000,"targetPort":5000,"type":"ClusterIP"}` | Service configuration |
| service.port | int | `5000` | Service port |
| service.targetPort | int | `5000` | Target port |
| service.type | string | `"ClusterIP"` | Service type |
| serviceAccount | object | `{"annotations":{},"create":false,"name":""}` | Service account configuration |
| serviceAccount.annotations | object | `{}` | Annotations to add to the service account |
| serviceAccount.create | bool | `false` | Specifies whether a service account should be created |
| serviceAccount.name | string | `""` | The name of the service account to use. If not set and create is true, a name is generated using the fullname template |
| serviceMonitor | object | `{"annotations":{},"enabled":false,"interval":"30s","labels":{},"scrapeTimeout":"10s"}` | ServiceMonitor for Prometheus Operator |
| serviceMonitor.annotations | object | `{}` | Annotations |
| serviceMonitor.enabled | bool | `false` | Enable ServiceMonitor |
| serviceMonitor.interval | string | `"30s"` | Scrape interval |
| serviceMonitor.labels | object | `{}` | Additional labels |
| serviceMonitor.scrapeTimeout | string | `"10s"` | Scrape timeout |
| tls | object | `{"certificate":"","enabled":false,"existingSecret":"","key":""}` | TLS configuration |
| tls.certificate | string | `""` | TLS certificate (if existingSecret is not set) |
| tls.enabled | bool | `false` | Enable TLS |
| tls.existingSecret | string | `""` | Use existing secret containing tls.crt and tls.key |
| tls.key | string | `""` | TLS private key (if existingSecret is not set) |
| tolerations | list | `[]` | Tolerations |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)

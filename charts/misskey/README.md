# misskey

![Version: 0.4.1](https://img.shields.io/badge/Version-0.4.1-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 2026.7.0](https://img.shields.io/badge/AppVersion-2026.7.0-informational?style=flat-square)

A Helm chart for Misskey - A decentralized social networking platform

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| externalPostgresql.database | string | `"misskey"` | External PostgreSQL database |
| externalPostgresql.existingSecret | string | `""` | Use existing secret for password (if set, misskey.existingConfigSecret is also required) |
| externalPostgresql.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| externalPostgresql.extra | object | `{}` | Extra connection options |
| externalPostgresql.host | string | `""` | External PostgreSQL host |
| externalPostgresql.password | string | `""` | External PostgreSQL password (required if existingSecret is not set) |
| externalPostgresql.port | int | `5432` | External PostgreSQL port |
| externalPostgresql.ssl | bool | `false` | Enable SSL |
| externalPostgresql.username | string | `"misskey"` | External PostgreSQL username |
| externalRedis.db | int | `0` | Redis database number |
| externalRedis.existingSecret | string | `""` | Use existing secret for password |
| externalRedis.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| externalRedis.family | int | `0` | IP family (0=Both, 4=IPv4, 6=IPv6) |
| externalRedis.host | string | `""` | External Redis host |
| externalRedis.password | string | `""` | External Redis password |
| externalRedis.port | int | `6379` | External Redis port |
| externalRedis.prefix | string | `""` | Key prefix |
| externalRedis.username | string | `""` | External Redis username (optional, used with ACL) |
| fullnameOverride | string | `""` | Override the full name of the chart |
| ingress.annotations | object | `{}` | Ingress annotations |
| ingress.className | string | `""` | Ingress class name |
| ingress.enabled | bool | `true` | Enable ingress |
| ingress.hosts | list | `[{"host":"misskey.example.com","paths":[{"path":"/","pathType":"Prefix"}]}]` | Ingress hosts |
| ingress.tls | list | `[]` | Ingress TLS configuration |
| migration.activeDeadlineSeconds | int | `600` | Maximum time in seconds for the migration Job to complete |
| migration.backoffLimit | int | `3` | Number of retries before marking the migration Job failed |
| migration.enabled | bool | `false` | Run database migrations as a Helm hook Job. When enabled, the web container starts with `pnpm run start` instead of the image default `pnpm run migrateandstart`. |
| migration.hookDeletePolicy | list | `["before-hook-creation","hook-succeeded"]` | Helm hook delete policy for the migration Job |
| migration.hookEvents | list | `["pre-upgrade"]` | Helm hook events for the migration Job. The default keeps install behavior unchanged. Add pre-install only when the database and config Secret are already available before chart install. |
| migration.resources | object | `{}` | Resource limits and requests for the migration container |
| misskey.allowedPrivateNetworks | list | `[]` | Allowed private networks for file upload |
| misskey.chmodSocket | string | `""` | Permission for socket file (e.g. '777') |
| misskey.clusterLimit | int | `1` | Number of worker processes (cluster limit) |
| misskey.deactivateAntennaThreshold | int | `604800000` | Threshold for deactivating antennas (milliseconds) |
| misskey.deliverJobConcurrency | string | `""` | Deliver job concurrency per worker |
| misskey.deliverJobMaxAttempts | string | `""` | Max attempts for deliver jobs |
| misskey.deliverJobPerSec | string | `""` | Deliver jobs per second |
| misskey.disableHsts | bool | `false` | Whether to disable HSTS |
| misskey.enableIpRateLimit | bool | `true` | Enable internal IP-based rate limiting (default: true) |
| misskey.existingConfigSecret | string | `""` | Use existing secret for the entire Misskey configuration (default.yml) If set, all other misskey.* settings are ignored and this secret is used |
| misskey.fulltextSearch | object | `{"provider":"sqlLike"}` | Fulltext search provider (sqlLike, sqlPgroonga, meilisearch) |
| misskey.id | string | `"aidx"` | ID generation method (aid, aidx, meid, ulid, objectid) ONCE YOU HAVE STARTED THE INSTANCE, DO NOT CHANGE THIS |
| misskey.inboxJobConcurrency | string | `""` | Inbox job concurrency per worker |
| misskey.inboxJobMaxAttempts | string | `""` | Max attempts for inbox jobs |
| misskey.inboxJobPerSec | string | `""` | Inbox jobs per second |
| misskey.logging.domains | object | `{}` | Per-logger domain log levels |
| misskey.logging.format | string | `""` | Log output format (pretty or json) |
| misskey.logging.level | string | `""` | Minimum log level (debug, info, warn, error, fatal, off) |
| misskey.logging.sql.disableQueryTruncation | bool | `false` | Disable query truncation in logs |
| misskey.logging.sql.enableQueryParamLogging | bool | `false` | Enable query parameter logging |
| misskey.maxFileSize | int | `262144000` | Maximum file size for uploads (bytes) |
| misskey.mediaProxy | string | `""` | Media proxy URL |
| misskey.meilisearch.apiKey | string | `""` |  |
| misskey.meilisearch.enabled | bool | `false` |  |
| misskey.meilisearch.host | string | `""` |  |
| misskey.meilisearch.index | string | `""` |  |
| misskey.meilisearch.port | int | `7700` |  |
| misskey.meilisearch.scope | string | `"local"` |  |
| misskey.meilisearch.ssl | bool | `false` |  |
| misskey.otelForBackend.capturePgConnectionSpans | bool | `false` | Include PostgreSQL connection-pool spans |
| misskey.otelForBackend.capturePgSpans | bool | `false` | Capture PostgreSQL query spans |
| misskey.otelForBackend.capturePgStatement | bool | `false` | Include raw SQL text in PostgreSQL spans |
| misskey.otelForBackend.captureRedisCommandSpans | bool | `false` | Capture Redis command spans |
| misskey.otelForBackend.captureRedisConnectionSpans | bool | `false` | Include Redis startup/reconnect spans |
| misskey.otelForBackend.captureRedisRootSpans | bool | `false` | Capture Redis commands without an HTTP or job parent span |
| misskey.otelForBackend.enabled | bool | `false` | Enable OpenTelemetry for backend |
| misskey.otelForBackend.endpoint | string | `""` | OTLP traces endpoint (e.g. http://localhost:4318/v1/traces) |
| misskey.otelForBackend.headers | object | `{}` | Headers to send with OTLP requests |
| misskey.otelForBackend.jobTraceContextMode | string | `"link"` | Queue worker trace context mode (link or parent) |
| misskey.otelForBackend.propagateTraceToRemote | bool | `false` | Propagate Sentry traces to remote outbound requests |
| misskey.otelForBackend.resourceAttributes | object | `{}` | Resource attributes to attach to traces |
| misskey.otelForBackend.sampleRate | float | `1` | Sampling rate |
| misskey.outgoingAddress | string | `""` | Local address used for outgoing requests |
| misskey.outgoingAddressFamily | string | `""` | IP address family for outgoing requests (ipv4, ipv6, dual) |
| misskey.perChannelMaxNoteCacheCount | int | `1000` | Maximum number of notes cached per channel |
| misskey.perUserNotificationsMaxCount | int | `500` | Maximum number of notifications cached per user |
| misskey.pidFile | string | `"/tmp/misskey.pid"` | PID file path |
| misskey.port | int | `3000` | Port for Misskey to listen on |
| misskey.proxy | string | `""` | Proxy settings for outgoing HTTP/HTTPS requests |
| misskey.proxyBypassHosts | list | `["api.deepl.com","api-free.deepl.com","www.recaptcha.net","hcaptcha.com","challenges.cloudflare.com"]` | Hosts that should bypass the proxy |
| misskey.proxySmtp | string | `""` | Proxy for SMTP/SMTPS connections |
| misskey.publishTarballInsteadOfProvideRepositoryUrl | bool | `false` | Publish tarball instead of repository URL (for AGPL compliance) |
| misskey.relationshipJobConcurrency | string | `""` | Relationship job concurrency per worker |
| misskey.relationshipJobPerSec | string | `""` | Relationship jobs per second |
| misskey.sentry.backend | object | `{"disabledIntegrations":[],"dsn":"","enableNodeProfiling":false,"enabled":false,"tracePropagationTargets":[]}` | Enable Sentry for backend |
| misskey.sentry.backend.disabledIntegrations | list | `[]` | Sentry integration names to disable |
| misskey.sentry.backend.tracePropagationTargets | list | `[]` | URL patterns to which Sentry trace headers are propagated |
| misskey.sentry.frontend | object | `{"browserTracingIntegration":{},"dsn":"","enabled":false,"replayIntegration":{},"vueIntegration":{}}` | Enable Sentry for frontend |
| misskey.sentry.frontend.browserTracingIntegration | object | `{}` | Browser tracing integration options |
| misskey.sentry.frontend.replayIntegration | object | `{}` | Replay integration options |
| misskey.sentry.frontend.vueIntegration | object | `{}` | Vue integration options |
| misskey.setupPassword | string | `""` | Setup password for initial admin account setup Be sure to change this when setting up Misskey via the Internet |
| misskey.socket | string | `""` | Use UNIX domain socket instead of port |
| misskey.threadPoolSize | int | `1` | Number of threads of extra thread pool for CPU-intensive tasks (per worker) |
| misskey.trustProxy | bool | `true` | Trust proxy settings for reverse proxy Can be boolean or array of CIDR ranges |
| misskey.url | string | `"https://misskey.example.tld"` | The URL of your Misskey instance (REQUIRED) IMPORTANT: Once you have started the instance, DO NOT CHANGE THIS |
| misskey.videoThumbnailGenerator | string | `""` | Video thumbnail generator URL |
| nameOverride | string | `""` | Override the name of the chart |
| postgresql.database | string | `"misskey"` | PostgreSQL database name |
| postgresql.disableCache | bool | `false` | Disable query caching |
| postgresql.enabled | bool | `true` | Enable internal PostgreSQL |
| postgresql.existingSecret | string | `""` | Use existing secret for password |
| postgresql.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| postgresql.extra | object | `{}` | Extra connection options |
| postgresql.image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| postgresql.image.repository | string | `"postgres"` | PostgreSQL image repository |
| postgresql.image.tag | string | `"18-alpine"` | PostgreSQL image tag |
| postgresql.password | string | `"you-should-change-this-password"` | PostgreSQL password (required if existingSecret is not set) |
| postgresql.persistence.accessModes | list | `["ReadWriteOnce"]` | Access modes |
| postgresql.persistence.enabled | bool | `true` | Enable persistence for PostgreSQL |
| postgresql.persistence.existingClaim | string | `""` | Existing claim to use |
| postgresql.persistence.size | string | `"10Gi"` | Size of the PVC |
| postgresql.persistence.storageClass | string | `""` | Storage class |
| postgresql.replication.enabled | bool | `false` | Enable database replication |
| postgresql.replication.slaves | list | `[]` | Slave database configurations Each slave can use existingSecret/existingSecretKey for password |
| postgresql.resources | object | `{}` | Resource limits and requests |
| postgresql.service.port | int | `5432` | PostgreSQL service port |
| postgresql.username | string | `"misskey"` | PostgreSQL username |
| redis.db | int | `0` | Redis database number |
| redis.enabled | bool | `true` | Enable internal Redis |
| redis.existingSecret | string | `""` | Use existing secret for password |
| redis.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| redis.family | int | `0` | IP family (0=Both, 4=IPv4, 6=IPv6) |
| redis.image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| redis.image.repository | string | `"redis"` | Redis image repository |
| redis.image.tag | string | `"7-alpine"` | Redis image tag |
| redis.password | string | `""` | Redis password (leave empty for no password) |
| redis.persistence.accessModes | list | `["ReadWriteOnce"]` | Access modes |
| redis.persistence.enabled | bool | `true` | Enable persistence for Redis |
| redis.persistence.existingClaim | string | `""` | Existing claim to use |
| redis.persistence.size | string | `"2Gi"` | Size of the PVC |
| redis.persistence.storageClass | string | `""` | Storage class |
| redis.prefix | string | `""` | Key prefix |
| redis.resources | object | `{}` | Resource limits and requests |
| redis.service.port | int | `6379` | Redis service port |
| redis.username | string | `""` | Redis username (optional, used with ACL) |
| redisForJobQueue.db | int | `0` | Redis database number |
| redisForJobQueue.enabled | bool | `false` | Enable separate Redis for Job Queue |
| redisForJobQueue.existingSecret | string | `""` | Use existing secret for password |
| redisForJobQueue.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| redisForJobQueue.family | int | `0` | IP family (0=Both, 4=IPv4, 6=IPv6) |
| redisForJobQueue.host | string | `""` | Redis host |
| redisForJobQueue.password | string | `""` | Redis password |
| redisForJobQueue.port | int | `6379` | Redis port |
| redisForJobQueue.prefix | string | `""` | Key prefix |
| redisForJobQueue.username | string | `""` | Redis username (optional, used with ACL) |
| redisForPubsub.db | int | `0` | Redis database number |
| redisForPubsub.enabled | bool | `false` | Enable separate Redis for PubSub |
| redisForPubsub.existingSecret | string | `""` | Use existing secret for password |
| redisForPubsub.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| redisForPubsub.family | int | `0` | IP family (0=Both, 4=IPv4, 6=IPv6) |
| redisForPubsub.host | string | `""` | Redis host |
| redisForPubsub.password | string | `""` | Redis password |
| redisForPubsub.port | int | `6379` | Redis port |
| redisForPubsub.prefix | string | `""` | Key prefix |
| redisForPubsub.username | string | `""` | Redis username (optional, used with ACL) |
| redisForReactions.db | int | `0` | Redis database number |
| redisForReactions.enabled | bool | `false` | Enable separate Redis for Reactions |
| redisForReactions.existingSecret | string | `""` | Use existing secret for password |
| redisForReactions.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| redisForReactions.family | int | `0` | IP family (0=Both, 4=IPv4, 6=IPv6) |
| redisForReactions.host | string | `""` | Redis host |
| redisForReactions.password | string | `""` | Redis password |
| redisForReactions.port | int | `6379` | Redis port |
| redisForReactions.prefix | string | `""` | Key prefix |
| redisForReactions.username | string | `""` | Redis username (optional, used with ACL) |
| redisForTimelines.db | int | `0` | Redis database number |
| redisForTimelines.enabled | bool | `false` | Enable separate Redis for Timelines |
| redisForTimelines.existingSecret | string | `""` | Use existing secret for password |
| redisForTimelines.existingSecretKey | string | `"password"` | Key in existing secret containing the password |
| redisForTimelines.family | int | `0` | IP family (0=Both, 4=IPv4, 6=IPv6) |
| redisForTimelines.host | string | `""` | Redis host |
| redisForTimelines.password | string | `""` | Redis password |
| redisForTimelines.port | int | `6379` | Redis port |
| redisForTimelines.prefix | string | `""` | Key prefix |
| redisForTimelines.username | string | `""` | Redis username (optional, used with ACL) |
| serviceAccount.annotations | object | `{}` | Annotations for service account |
| serviceAccount.create | bool | `true` | Create service account |
| serviceAccount.name | string | `""` | Service account name |
| web.affinity | object | `{}` | Affinity rules |
| web.diagnosticReport.compact | bool | `true` | Write compact single-line JSON reports |
| web.diagnosticReport.directory | string | `"/var/run/misskey-node-reports"` | Directory where Node.js diagnostic report files are written |
| web.diagnosticReport.enabled | bool | `false` | Enable Node.js diagnostic reports for the Misskey web process |
| web.diagnosticReport.reportOnFatalError | bool | `true` | Write a report on fatal Node.js/V8 errors |
| web.diagnosticReport.reportOnSignal | bool | `true` | Write a report when the diagnostic signal is received |
| web.diagnosticReport.reportOnUncaughtException | bool | `true` | Write a report on uncaught exceptions |
| web.diagnosticReport.signal | string | `"SIGUSR2"` | Signal that triggers report generation when reportOnSignal is enabled |
| web.diagnosticReport.sizeLimit | string | `"256Mi"` | EmptyDir size limit for diagnostic report files |
| web.extraEnv | list | `[]` | Additional environment variables |
| web.extraEnvFrom | list | `[]` | Additional environment variables from secrets/configmaps |
| web.extraVolumeMounts | list | `[]` | Additional volume mounts added to the Misskey container |
| web.extraVolumes | list | `[]` | Additional volumes added to the web Pod |
| web.image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| web.image.repository | string | `"misskey/misskey"` | Image repository |
| web.image.tag | string | `""` | Image tag (defaults to Chart.appVersion) |
| web.imagePullSecrets | list | `[]` | Image pull secrets |
| web.livenessProbe | object | `{"failureThreshold":4,"httpGet":{"path":"/healthz","port":"http"},"initialDelaySeconds":30,"periodSeconds":30,"timeoutSeconds":15}` | Liveness probe configuration |
| web.nodeSelector | object | `{}` | Node selector |
| web.persistence.accessModes | list | `["ReadWriteOnce"]` | Access modes |
| web.persistence.annotations | object | `{}` | Annotations for the PVC |
| web.persistence.enabled | bool | `true` | Enable persistence for files |
| web.persistence.existingClaim | string | `""` | Existing claim to use |
| web.persistence.size | string | `"10Gi"` | Size of the PVC |
| web.persistence.storageClass | string | `""` | Storage class |
| web.podAnnotations | object | `{}` | Pod annotations |
| web.podLabels | object | `{}` | Pod labels |
| web.podSecurityContext | object | `{"fsGroup":991}` | Security context for the pod |
| web.readinessProbe | object | `{"failureThreshold":3,"httpGet":{"path":"/healthz","port":"http"},"initialDelaySeconds":15,"periodSeconds":10,"timeoutSeconds":5}` | Readiness probe configuration |
| web.replicaCount | int | `1` | Number of replicas |
| web.resources | object | `{}` | Resource limits and requests |
| web.securityContext | object | `{"runAsGroup":991,"runAsNonRoot":true,"runAsUser":991}` | Security context for the container |
| web.service.port | int | `3000` | Service port |
| web.service.type | string | `"ClusterIP"` | Service type |
| web.startupProbe | object | `{"failureThreshold":30,"httpGet":{"path":"/healthz","port":"http"},"initialDelaySeconds":15,"periodSeconds":10,"timeoutSeconds":5}` | Startup probe configuration |
| web.tolerations | list | `[]` | Tolerations |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)

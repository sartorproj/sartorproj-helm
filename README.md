# Sartor Helm Chart

Helm chart for [Sartor](https://github.com/sartorproj/sartor), a Kubernetes controller that recommends CPU/memory requests from Prometheus-compatible metrics (Prometheus, VictoriaMetrics) and opens GitOps pull requests.

The chart installs:

- **CRDs**: Atelier (cluster-scoped), FitProfile and Tailoring, rendered from `files/crds` (copied verbatim from `sartorproj/sartor` `config/crd/bases`).
- **Controller** (`ghcr.io/sartorproj/sartor`): always on.
- **API server** (`ghcr.io/sartorproj/sartor-server`): optional, off by default.
- **UI** (`ghcr.io/sartorproj/sartor-ui`): optional, off by default, requires the server.

## Safe defaults

A default install is **recommend-only**: the controller analyses workloads and records recommendations in `Tailoring` status. It does not patch workloads or open pull requests. Each write path is a separate opt-in:

| Value | Controller flag | Default | What it allows | Extra RBAC |
|-------|-----------------|---------|----------------|------------|
| `controller.oomPatching.enabled` | `--enable-oom-patching` | `false` | Raise memory limits of OOM-killed containers in workloads with a non-paused Tailoring | `pods` get/list/watch; `deployments`, `statefulsets`, `daemonsets` update/patch (cluster-wide) |
| `controller.gitWriteback.enabled` | `--enable-git-writeback` | `false` | Open/update one PR per non-paused Tailoring | none (writes go to the git provider) |
| `controller.analysisInterval` | `--analysis-interval` | `6h` | How often a Tailoring is re-analysed | - |

The flags are always rendered explicitly. `controller.extraArgs` cannot override them; the chart fails to render instead.

> These three flags need a controller build that includes them (sartorproj/sartor PRs for the OOM opt-in, analysis interval and git write-back). Older controller images exit with `flag provided but not defined`.

## Install

The chart is not published to a Helm repository yet. Install from a checkout:

```bash
helm install sartor . -n sartor-system --create-namespace
```

When a version is published from `main` or a tag, it is available as an OCI chart:

```bash
helm install sartor oci://ghcr.io/sartorproj/charts/sartor --version 0.2.0 -n sartor-system --create-namespace
```

The chart deploys into the release namespace (`namespaceOverride` changes it). It does not create the namespace; use `--create-namespace`.

## RBAC

The controller gets:

- **ClusterRole**: get/list/watch/update/patch on Sartor CRs and their status/finalizers. Read-only on deployments/statefulsets/daemonsets, HPAs and resource quotas. `tokenreviews`/`subjectaccessreviews` create when `controller.metrics.secure=true`.
- **Role in the release namespace**: `get` on Secrets, leader-election leases, and events.

There is no cluster-wide access to Secrets, and no `list`/`watch` on Secrets anywhere. Credentials referenced by an Atelier (`prometheus.secretRef`, `gitProvider.secretRef`, `argocd.secretRef`) must therefore live in the release namespace.

Workload `update`/`patch` and `pods` read are granted only when `controller.oomPatching.enabled=true`. `argoproj.io` Applications access is granted only when `argocd.enabled=true`.

The server, when enabled, gets read-only access to Sartor CRs, `get` on Secrets in the release namespace, and Applications access only when `argocd.enabled=true`.

## Metrics

The controller serves metrics on `:8443` over HTTPS, with Kubernetes authentication and authorization (`--metrics-secure=true`) and a self-signed certificate. Scrapers need:

1. a bearer token whose service account is bound to the `<fullname>-metrics-reader` ClusterRole (`controller.metrics.readerSubjects` creates the binding), and
2. `tlsConfig.insecureSkipVerify: true` (the ServiceMonitor default) or your own CA.

Set `controller.metrics.secure=false` (and usually `controller.metrics.port=8080`) for plain HTTP.

`controller.metrics.serviceMonitor.enabled` creates a prometheus-operator `ServiceMonitor` (`monitoring.coreos.com/v1`); the prometheus-operator CRDs must be installed. The chart ships no other monitoring objects: the VictoriaMetrics operator converts ServiceMonitors into its own scrape objects automatically.

## CRDs

CRDs are rendered as templates rather than from `crds/`, so `helm upgrade` updates them. With `crds.keep=true` (the default) they are annotated `helm.sh/resource-policy: keep`, so `helm uninstall` never deletes them or the Tailorings stored in them. Set `crds.install=false` if you manage CRDs elsewhere.

A later reinstall must use the same release name and namespace to adopt the kept CRDs. Otherwise delete them first:

```bash
kubectl delete crd ateliers.autoscaling.sartorproj.io fitprofiles.autoscaling.sartorproj.io tailorings.autoscaling.sartorproj.io
```

## Values

`values.schema.json` validates values. Unknown keys under chart-owned objects are rejected, so a typo such as `oomPatching.enable` fails instead of being ignored.

| Parameter | Description | Default |
|-----------|-------------|---------|
| `namespaceOverride` | Namespace to deploy into | release namespace |
| `clusterDomain` | Cluster DNS domain for fully qualified Service names | `cluster.local` |
| `global.imagePullSecrets` | Pull secrets for all pods | `[]` |
| `crds.install` / `crds.keep` | Render CRDs / keep them on uninstall | `true` / `true` |
| `rbac.create` | Create (Cluster)Roles and bindings | `true` |
| `controller.image.repository` | Controller image | `ghcr.io/sartorproj/sartor` |
| `controller.image.tag` | Image tag | chart `appVersion` |
| `controller.image.digest` | `sha256:` digest; pins the image | `""` |
| `controller.replicaCount` | Replicas (more than 1 needs leader election) | `1` |
| `controller.leaderElection.enabled` | `--leader-elect` | `true` |
| `controller.oomPatching.enabled` | `--enable-oom-patching` | `false` |
| `controller.gitWriteback.enabled` | `--enable-git-writeback` | `false` |
| `controller.analysisInterval` | `--analysis-interval` (Go duration) | `6h` |
| `controller.metrics.enabled` / `port` / `secure` | Metrics server | `true` / `8443` / `true` |
| `controller.metrics.readerSubjects` | Service accounts bound to the metrics-reader ClusterRole | `[]` |
| `controller.metrics.serviceMonitor.*` | prometheus-operator ServiceMonitor | disabled |
| `controller.extraArgs` | Additional flags (chart-managed flags are rejected) | `[]` |
| `controller.resources` | Requests/limits | `100m`/`128Mi` requests, `500m`/`512Mi` limits |
| `controller.podSecurityContext` / `securityContext` | Non-root UID/GID 65532, read-only root FS, drop ALL, seccomp `RuntimeDefault` | hardened |
| `controller.nodeSelector` / `tolerations` / `affinity` | Scheduling | empty |
| `argocd.enabled` | Grant Argo CD Applications access | `false` |
| `server.enabled` | Deploy the API server | `false` |
| `server.apiKeySecretName` | Secret with API keys (release namespace). Without it the API is unauthenticated. | `sartor-api-keys` |
| `server.cors` | `--enable-cors` | `false` |
| `server.ingress.*` | Server Ingress | disabled |
| `ui.enabled` | Deploy the UI (requires `server.enabled`) | `false` |
| `ui.apiUrl` | `SARTOR_API_URL` override | fully qualified server Service |
| `ui.apiKey.existingSecret` / `existingSecretKey` | Existing Secret for `SARTOR_API_KEY` | `""` / `api-key` |
| `ui.apiKey.value` | Chart-created Secret for `SARTOR_API_KEY` (prefer `existingSecret`) | `""` |
| `ui.ingress.*` | UI Ingress | disabled |

See [`values.yaml`](values.yaml) for every option.

### UI

The `sartor-ui` image is nginx-unprivileged and serves the app plus a same-origin `/api/` proxy to the server. Its pod runs with:

- UID 101 and `runAsNonRoot` (any non-root UID works);
- a read-only root filesystem, with an emptyDir mounted at `/tmp`;
- all capabilities dropped.

- **API URL.** `SARTOR_API_URL` is a plain env value on the UI Deployment. The image has no default and answers 502 on `/api/` until the upstream resolves. It defaults to the fully qualified server Service, `http://<fullname>-server.<namespace>.svc.<clusterDomain>:<server.service.port>`, because nginx's runtime resolver skips DNS search domains. Set `clusterDomain` if your cluster doesn't use `cluster.local`, or `ui.apiUrl` to override the URL. Changing it rolls the pod.
- **API key (optional).** `SARTOR_API_KEY` comes from a Secret through `secretKeyRef`:
  - `ui.apiKey.existingSecret` and `ui.apiKey.existingSecretKey` reference your own Secret (preferred);
  - `ui.apiKey.value` makes the chart create one. A `checksum/secret` pod annotation rolls the pod when the value changes.
- **Access control.** Deploy the UI on a private network or behind an authenticating proxy; the server-side key proxy gives the API to anyone who can reach the UI. **Never expose it publicly**, and never expose the server's own Ingress publicly either.

## Development

CI (`.github/workflows/ci.yml`) runs `helm lint --strict`, renders the default values with `helm template` and validates them with `kubeconform -strict`, and packages the chart. Run the same checks locally:

```bash
helm lint --strict .
helm template sartor . -n sartor-system | kubeconform -strict -summary -skip CustomResourceDefinition
```

## License

Apache License 2.0. See [LICENSE](LICENSE).

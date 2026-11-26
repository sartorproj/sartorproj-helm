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

### Staging with VictoriaMetrics

```yaml
# values-staging.yaml
controller:
  metrics:
    readerSubjects:
      - name: vmagent-vmagent      # vmagent's service account
        namespace: monitoring
    vmServiceScrape:
      enabled: true
  rbac:
    secretNames:                   # optional: restrict Secret reads to these names
      - github-token
```

```bash
helm install sartor . -n sartor-system --create-namespace -f values-staging.yaml
```

Then create the Atelier with the vmselect URL. Controller builds with the sartorproj/sartor VictoriaMetrics fix keep the path prefix and the query string (e.g. `extra_label`) on every request:

```yaml
apiVersion: autoscaling.sartorproj.io/v1alpha1
kind: Atelier
metadata:
  name: default
spec:
  prometheus:
    url: http://vmselect.monitoring:8481/select/0/prometheus?extra_label=cluster=staging-base-cluster
  gitProvider:
    type: github
    secretRef:
      name: github-token
      namespace: sartor-system     # must be the release namespace
  defaultAnalysisWindow: 168h
```

## RBAC

The controller gets:

- **ClusterRole**: get/list/watch/update/patch on Sartor CRs and their status/finalizers. Read-only on deployments/statefulsets/daemonsets, HPAs and resource quotas. `tokenreviews`/`subjectaccessreviews` create when `controller.metrics.secure=true`.
- **Role in the release namespace**: `get` on Secrets (optionally limited by `controller.rbac.secretNames`), leader-election leases, and events.

There is no cluster-wide access to Secrets, and no `list`/`watch` on Secrets anywhere. Credentials referenced by an Atelier (`prometheus.secretRef`, `gitProvider.secretRef`, `argocd.secretRef`) must therefore live in the release namespace.

Workload `update`/`patch` and `pods` read are granted only when `controller.oomPatching.enabled=true`. `argoproj.io` Applications access is granted only when `argocd.enabled=true`.

The server, when enabled, gets read-only access to Sartor CRs, `get` on Secrets in the release namespace, and Applications access only when `argocd.enabled=true`.

## Metrics

The controller serves metrics on `:8443` over HTTPS, with Kubernetes authentication and authorization (`--metrics-secure=true`) and a self-signed certificate. Scrapers need:

1. a bearer token whose service account is bound to the `<fullname>-metrics-reader` ClusterRole (`controller.metrics.readerSubjects` creates the binding), and
2. `tlsConfig.insecureSkipVerify: true` (the default in both monitor objects) or your own CA.

Set `controller.metrics.secure=false` (and usually `controller.metrics.port=8080`) for plain HTTP.

`controller.metrics.serviceMonitor.enabled` creates a prometheus-operator `ServiceMonitor`. `controller.metrics.vmServiceScrape.enabled` creates a VictoriaMetrics operator `VMServiceScrape`. Each needs its operator's CRDs installed.

## CRDs

CRDs are rendered as templates rather than from `crds/`, so `helm upgrade` updates them. With `crds.keep=true` (the default) they are annotated `helm.sh/resource-policy: keep`, so `helm uninstall` never deletes them or the Tailorings stored in them. Set `crds.install=false` if you manage CRDs elsewhere.

A later reinstall must use the same release name and namespace to adopt the kept CRDs. Otherwise delete them first:

```bash
kubectl delete crd ateliers.autoscaling.sartorproj.io fitprofiles.autoscaling.sartorproj.io tailorings.autoscaling.sartorproj.io
```

`files/crds` must match `config/crd/bases` on `sartorproj/sartor` `main`; CI fails otherwise. To sync:

```bash
hack/sync-crds.sh ../sartor
```

## Values

`values.schema.json` validates values. Unknown keys under chart-owned objects are rejected, so a typo such as `oomPatching.enable` fails instead of being ignored.

| Parameter | Description | Default |
|-----------|-------------|---------|
| `namespaceOverride` | Namespace to deploy into | release namespace |
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
| `controller.metrics.vmServiceScrape.*` | VictoriaMetrics VMServiceScrape | disabled |
| `controller.log.level` / `encoder` / `development` | zap logging flags | `info` / `json` / `false` |
| `controller.rbac.secretNames` | Restrict Secret `get` to these names | `[]` (any Secret in the namespace) |
| `controller.extraArgs` | Additional flags (chart-managed flags are rejected) | `[]` |
| `controller.resources` | Requests/limits | `100m`/`128Mi` requests, `512Mi` memory limit |
| `controller.podSecurityContext` / `securityContext` | Non-root UID/GID 65532, read-only root FS, drop ALL, seccomp `RuntimeDefault` | hardened |
| `controller.nodeSelector` / `tolerations` / `affinity` / `topologySpreadConstraints` / `priorityClassName` | Scheduling | empty |
| `argocd.enabled` | Grant Argo CD Applications access | `false` |
| `server.enabled` | Deploy the API server | `false` |
| `server.apiKeySecretName` | Secret with API keys (release namespace). Without it the API is unauthenticated. | `sartor-api-keys` |
| `server.cors` | `--enable-cors` | `false` |
| `server.ingress.*` | Server Ingress | disabled |
| `ui.enabled` | Deploy the UI (requires `server.enabled`) | `false` |
| `ui.ingress.*` | UI Ingress | disabled |

See [`values.yaml`](values.yaml) for every option.

### UI caveats

The current `sartor-ui` image is `nginx:alpine`. It starts as root and proxies `/api` to the hard-coded host `http://sartor-server:8080`. Consequences:

- The UI pod does not run with `runAsNonRoot` or a read-only root filesystem. It keeps `CHOWN`, `SETUID` and `SETGID` so the nginx workers can drop privileges.
- The release must be named `sartor` (or set `fullnameOverride: sartor`) for the UI to reach the server.

## Development

CI (`.github/workflows/ci.yml`) runs:

- `helm lint --strict` for every `ci/*-values.yaml`.
- `helm template` for every values file on Kubernetes 1.28 and 1.31, followed by:
  - `hack/check-manifests.py`, which checks that RBAC matches the rendered flags, Secrets are only `get` in a Role, there are no wildcards, and security contexts are hardened;
  - `kubeconform -strict`, including ServiceMonitor/VMServiceScrape schemas.
- Rejection tests for invalid values.
- `ct lint`.
- A CRD sync check against `sartorproj/sartor` `main`.
- A kind install, upgrade and uninstall. This validates objects against a real API server, asserts the effective RBAC with `kubectl auth can-i` (`hack/check-rbac.sh`), and checks that CRDs survive uninstall.

Run the same checks locally:

```bash
for f in ci/*-values.yaml; do
  helm lint --strict . -f "$f"
  helm template sartor . -n sartor-system -f "$f" | tee /tmp/out.yaml | hack/check-manifests.py
  kubeconform -strict -summary -skip CustomResourceDefinition \
    -schema-location default \
    -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
    /tmp/out.yaml
done
```

## License

Apache License 2.0. See [LICENSE](LICENSE).

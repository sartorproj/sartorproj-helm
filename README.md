# Sartor Helm Chart

A Helm chart for deploying [Sartor](https://github.com/sartorproj/sartor) - Kubernetes resource optimization via GitOps.

## Overview

Sartor analyzes your workload metrics and creates pull requests to right-size container resources. This chart installs:

- **Controller**: Watches Tailoring resources and generates recommendations
- **Server**: REST API for the UI and external integrations
- **UI**: Web dashboard for visualizing recommendations
- **CRDs**: Custom Resource Definitions (Atelier, FitProfile, Tailoring)

## Prerequisites

- Kubernetes 1.21+
- Helm 3.0+
- Prometheus (for metrics collection)
- Git provider (GitHub, GitLab, or Bitbucket)

## Installation

### Add the Helm repository

```bash
helm repo add sartor https://sartorproj.github.io/sartorproj-helm
helm repo update
```

### Install the chart

```bash
helm install sartor sartor/sartor -n sartor-system --create-namespace
```

### Install from local directory

```bash
helm install sartor . -n sartor-system --create-namespace
```

## Configuration

### Global Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `global.namespace` | Namespace to deploy Sartor | `sartor-system` |
| `global.createNamespace` | Create the namespace | `true` |
| `global.imagePullSecrets` | Image pull secrets for all components | `[]` |

### CRDs

| Parameter | Description | Default |
|-----------|-------------|---------|
| `crds.install` | Install CRDs | `true` |
| `crds.keep` | Keep CRDs on uninstall | `true` |

### Controller

| Parameter | Description | Default |
|-----------|-------------|---------|
| `controller.enabled` | Enable the controller | `true` |
| `controller.replicaCount` | Number of replicas | `1` |
| `controller.image.repository` | Controller image repository | `ghcr.io/sartorproj/sartor-controller` |
| `controller.image.tag` | Controller image tag | `""` (uses appVersion) |
| `controller.resources.requests.cpu` | CPU request | `100m` |
| `controller.resources.requests.memory` | Memory request | `128Mi` |
| `controller.resources.limits.cpu` | CPU limit | `500m` |
| `controller.resources.limits.memory` | Memory limit | `512Mi` |

### Server

| Parameter | Description | Default |
|-----------|-------------|---------|
| `server.enabled` | Enable the server | `true` |
| `server.replicaCount` | Number of replicas | `1` |
| `server.image.repository` | Server image repository | `ghcr.io/sartorproj/sartor-server` |
| `server.image.tag` | Server image tag | `""` (uses appVersion) |
| `server.service.type` | Service type | `ClusterIP` |
| `server.service.port` | Service port | `8080` |
| `server.ingress.enabled` | Enable ingress | `false` |
| `server.ingress.className` | Ingress class name | `""` |
| `server.ingress.hosts` | Ingress hosts | `[{host: sartor-api.local, paths: [{path: /, pathType: Prefix}]}]` |

### UI

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ui.enabled` | Enable the UI | `true` |
| `ui.replicaCount` | Number of replicas | `1` |
| `ui.image.repository` | UI image repository | `ghcr.io/sartorproj/sartor-ui` |
| `ui.image.tag` | UI image tag | `""` (uses appVersion) |
| `ui.service.type` | Service type | `ClusterIP` |
| `ui.service.port` | Service port | `5173` |
| `ui.ingress.enabled` | Enable ingress | `false` |
| `ui.ingress.className` | Ingress class name | `""` |
| `ui.ingress.hosts` | Ingress hosts | `[{host: sartor.local, paths: [{path: /, pathType: Prefix}]}]` |

### RBAC

| Parameter | Description | Default |
|-----------|-------------|---------|
| `rbac.create` | Create RBAC resources | `true` |
| `serviceAccount.create` | Create service accounts | `true` |
| `serviceAccount.annotations` | Service account annotations | `{}` |

## Example Values

### Basic installation

```yaml
global:
  namespace: sartor-system

controller:
  enabled: true

server:
  enabled: true

ui:
  enabled: true
```

### With Ingress

```yaml
server:
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: sartor-api.example.com
        paths:
          - path: /
            pathType: Prefix
    tls:
      - secretName: sartor-api-tls
        hosts:
          - sartor-api.example.com

ui:
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: sartor.example.com
        paths:
          - path: /
            pathType: Prefix
    tls:
      - secretName: sartor-ui-tls
        hosts:
          - sartor.example.com
```

### Production settings

```yaml
controller:
  replicaCount: 2
  resources:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      cpu: "1"
      memory: 1Gi
  args:
    - --metrics-bind-address=:8080
    - --health-probe-bind-address=:8081
    - --leader-elect=true

server:
  replicaCount: 2
  resources:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      cpu: "1"
      memory: 1Gi
```

## Uninstallation

```bash
helm uninstall sartor -n sartor-system
```

**Note**: CRDs are not deleted by default. To remove CRDs:

```bash
kubectl delete crd ateliers.autoscaling.sartorproj.io
kubectl delete crd fitprofiles.autoscaling.sartorproj.io
kubectl delete crd tailorings.autoscaling.sartorproj.io
```

## Getting Started

After installation, you need to:

1. **Create a Git provider secret** with your access token:

```bash
kubectl create secret generic github-token \
  --from-literal=token=ghp_xxxxxxxxxxxx \
  -n sartor-system
```

2. **Create an Atelier** to configure Sartor:

```yaml
apiVersion: autoscaling.sartorproj.io/v1alpha1
kind: Atelier
metadata:
  name: default
spec:
  prometheus:
    url: http://prometheus.monitoring:9090
  gitProvider:
    type: github
    secretRef:
      name: github-token
      namespace: sartor-system
```

3. **Create a FitProfile** for your optimization strategy:

```yaml
apiVersion: autoscaling.sartorproj.io/v1alpha1
kind: FitProfile
metadata:
  name: balanced
  namespace: default
spec:
  strategy: percentile
  displayName: Balanced
  parameters:
    percentile: 95
    requestsBufferPercent: 15
    limitsBufferPercent: 25
```

4. **Create a Tailoring** to optimize a workload:

```yaml
apiVersion: autoscaling.sartorproj.io/v1alpha1
kind: Tailoring
metadata:
  name: my-app
  namespace: default
spec:
  target:
    kind: Deployment
    name: my-app
  fitProfileRef:
    name: balanced
  writeBack:
    type: raw
    repository: github.com/myorg/myrepo
    path: k8s/deployment.yaml
```

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details.

## Support

- [Documentation](https://github.com/sartorproj/sartor/tree/main/docs)
- [Issues](https://github.com/sartorproj/sartor/issues)

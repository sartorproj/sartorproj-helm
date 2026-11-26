#!/usr/bin/env bash
# Exercise the UI wiring of an installed release on a live cluster.
# Usage: hack/check-ui-kind.sh <namespace> <release>   (release fullname must equal the release name)
# Pods are not expected to become ready (images may be unpublished); the checks use the Deployment,
# ReplicaSets and Pod objects the API server creates.
set -euo pipefail

ns="$1"
release="$2"
deploy="${release}-ui"
chart_dir="$(cd "$(dirname "$0")/.." && pwd)"

fail() { echo "FAIL  $*" >&2; exit 1; }
ok() { echo "ok    $*"; }

env_value() {
  kubectl get deploy "$deploy" -n "$ns" -o jsonpath="{.spec.template.spec.containers[0].env[?(@.name==\"$1\")]$2}"
}

# pod-template-hash of the Deployment's newest ReplicaSet
newest_hash() {
  kubectl get rs -n "$ns" -l "app.kubernetes.io/name=sartor-ui,app.kubernetes.io/instance=${release}" \
    -o jsonpath='{range .items[*]}{.metadata.annotations.deployment\.kubernetes\.io/revision} {.metadata.labels.pod-template-hash}{"\n"}{end}' \
    | sort -n | tail -1 | cut -d' ' -f2
}

wait_rollout_object() {
  local hash
  for _ in $(seq 1 30); do
    kubectl get deploy "$deploy" -n "$ns" -o jsonpath='{.metadata.generation} {.status.observedGeneration}' \
      | awk '{exit !($1 == $2)}' && break
    sleep 2
  done
  hash="$(newest_hash)"
  for _ in $(seq 1 30); do
    if [ -n "$(kubectl get pods -n "$ns" -l "pod-template-hash=${hash}" -o name)" ]; then
      echo "$hash"
      return 0
    fi
    sleep 2
  done
  fail "no Pod created for ReplicaSet hash ${hash}"
}

upgrade() {
  helm upgrade "$release" "$chart_dir" -n "$ns" "$@" > /dev/null
}

fqdn="http://${release}-server.${ns}.svc.cluster.local:8080"

# 1. Default upstream: fully qualified server Service as a plain env value, no API key.
upgrade --reset-values --set server.enabled=true --set ui.enabled=true
[ "$(env_value SARTOR_API_URL .value)" = "$fqdn" ] || fail "SARTOR_API_URL = $(env_value SARTOR_API_URL .value), want $fqdn"
[ -z "$(env_value SARTOR_API_URL .valueFrom)" ] || fail "SARTOR_API_URL must be a plain value"
[ -z "$(env_value SARTOR_API_KEY .name)" ] || fail "SARTOR_API_KEY must be absent without a key"
ok "default SARTOR_API_URL=$fqdn, no SARTOR_API_KEY"
h1="$(wait_rollout_object)"

# 2. Changing ui.apiUrl rolls the pod.
custom="http://sartor-api.example.svc.cluster.local:9090"
upgrade --reuse-values --set ui.apiUrl="$custom"
[ "$(env_value SARTOR_API_URL .value)" = "$custom" ] || fail "SARTOR_API_URL not updated"
h2="$(wait_rollout_object)"
[ "$h2" != "$h1" ] || fail "ui.apiUrl change did not create a new ReplicaSet"
ok "ui.apiUrl change rolled ReplicaSet ${h1} -> ${h2}"

# 3. existingSecret: secretKeyRef points at the user's Secret and key; no chart Secret.
kubectl create secret generic ui-key -n "$ns" --from-literal=token=placeholder --dry-run=client -o yaml | kubectl apply -f - > /dev/null
upgrade --reuse-values --set ui.apiKey.existingSecret=ui-key --set ui.apiKey.existingSecretKey=token
[ "$(env_value SARTOR_API_KEY .valueFrom.secretKeyRef.name)" = "ui-key" ] || fail "SARTOR_API_KEY secret name"
[ "$(env_value SARTOR_API_KEY .valueFrom.secretKeyRef.key)" = "token" ] || fail "SARTOR_API_KEY secret key"
if kubectl get secret "${deploy}-api-key" -n "$ns" > /dev/null 2>&1; then fail "chart Secret must not exist with existingSecret"; fi
h3="$(wait_rollout_object)"
pod="$(kubectl get pods -n "$ns" -l "pod-template-hash=${h3}" -o name | head -1)"
[ "$(kubectl get "$pod" -n "$ns" -o jsonpath='{.spec.containers[0].env[?(@.name=="SARTOR_API_KEY")].valueFrom.secretKeyRef.name}')" = "ui-key" ] \
  || fail "new Pod does not reference ui-key"
ok "existingSecret ui-key/token wired into ${pod}"

# 4. Chart-created Secret: checksum/secret changes with the value and rolls the pod.
checksum() {
  kubectl get deploy "$deploy" -n "$ns" -o json \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["spec"]["template"]["metadata"].get("annotations", {}).get("checksum/secret", ""))'
}
secret_value() {
  kubectl get secret "$1" -n "$ns" -o json \
    | python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["data"][sys.argv[1]]).decode())' "$2"
}
upgrade --reuse-values --set ui.apiKey.existingSecret= --set ui.apiKey.value=placeholder-one
[ "$(env_value SARTOR_API_KEY .valueFrom.secretKeyRef.name)" = "${deploy}-api-key" ] || fail "SARTOR_API_KEY not pointing at the chart Secret"
[ "$(secret_value "${deploy}-api-key" api-key)" = "placeholder-one" ] || fail "chart Secret content"
c1="$(checksum)"
[ -n "$c1" ] || fail "checksum/secret annotation missing"
h4="$(wait_rollout_object)"
upgrade --reuse-values --set ui.apiKey.value=placeholder-two
c2="$(checksum)"
[ "$c2" != "$c1" ] || fail "checksum/secret did not change"
h5="$(wait_rollout_object)"
[ "$h5" != "$h4" ] || fail "Secret change did not create a new ReplicaSet"
ok "chart Secret checksum rolled ReplicaSet ${h4} -> ${h5}"

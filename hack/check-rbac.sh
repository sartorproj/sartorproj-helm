#!/usr/bin/env bash
# Assert effective RBAC of an installed release with `kubectl auth can-i`.
# Usage: hack/check-rbac.sh <namespace> <release-fullname> <oom-patching: true|false> [server: true|false]
set -euo pipefail

ns="$1"
fullname="$2"
oom="$3"
server="${4:-false}"
fails=0

expect() {
  local who="$1" want="$2"
  shift 2
  local got
  got="$(kubectl auth can-i "$@" --as="system:serviceaccount:${ns}:${who}" 2>/dev/null || true)"
  got="${got%% *}"
  if [ "$got" = "$want" ]; then
    echo "ok    ${who}: can-i $* = ${got}"
  else
    echo "FAIL  ${who}: can-i $* = ${got:-<empty>}, want ${want}"
    fails=1
  fi
}

ctrl="${fullname}-controller"
if [ "$oom" = "true" ]; then workload=yes; else workload=no; fi

expect "$ctrl" yes get secrets -n "$ns"
expect "$ctrl" no list secrets -n "$ns"
expect "$ctrl" no watch secrets -n "$ns"
expect "$ctrl" no get secrets -n default
expect "$ctrl" no list secrets --all-namespaces
expect "$ctrl" no create secrets -n "$ns"
expect "$ctrl" yes list tailorings.autoscaling.sartorproj.io --all-namespaces
expect "$ctrl" yes update tailorings.autoscaling.sartorproj.io --subresource=status -n default
expect "$ctrl" no delete tailorings.autoscaling.sartorproj.io -n default
expect "$ctrl" yes list deployments.apps --all-namespaces
expect "$ctrl" "$workload" patch deployments.apps -n default
expect "$ctrl" "$workload" update statefulsets.apps -n default
expect "$ctrl" "$workload" update daemonsets.apps -n default
expect "$ctrl" "$workload" list pods --all-namespaces
expect "$ctrl" no delete deployments.apps -n default
expect "$ctrl" no patch pods -n default
expect "$ctrl" yes update leases.coordination.k8s.io -n "$ns"
expect "$ctrl" no update leases.coordination.k8s.io -n default
expect "$ctrl" yes create tokenreviews.authentication.k8s.io

if [ "$server" = "true" ]; then
  srv="${fullname}-server"
  expect "$srv" yes get secrets -n "$ns"
  expect "$srv" no list secrets -n "$ns"
  expect "$srv" no get secrets -n default
  expect "$srv" yes list tailorings.autoscaling.sartorproj.io --all-namespaces
  expect "$srv" no update tailorings.autoscaling.sartorproj.io -n default
  expect "$srv" no patch deployments.apps -n default
fi

exit "$fails"

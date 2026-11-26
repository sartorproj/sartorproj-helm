#!/usr/bin/env python3
"""Policy checks for rendered chart manifests.

Usage: helm template ... | hack/check-manifests.py

Ties RBAC to the flags actually passed to the controller, so a values change
cannot grant workload writes or broaden Secret access without failing CI.
"""
import sys

import yaml

WORKLOADS = {"deployments", "statefulsets", "daemonsets"}
# Container name -> required UID (None: any non-root UID).
HARDENED = {"manager": 65532, "server": 65532, "ui": None}

errors = []


def fail(msg):
    errors.append(msg)


def rules_of(doc):
    return doc.get("rules") or []


def grants(rule, group, resource, verbs):
    return (
        group in rule.get("apiGroups", [])
        and resource in rule.get("resources", [])
        and bool(set(verbs) & set(rule.get("verbs", [])))
    )


def flag(args, name):
    """Value of a Go flag as the binary sees it: the last occurrence wins, any dash form."""
    value = None
    for a in args:
        key, _, val = a.lstrip("-").partition("=")
        if a.startswith("-") and key == name:
            value = val if val else "true"
    return value


docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
by_kind = {}
for d in docs:
    by_kind.setdefault(d["kind"], []).append(d)

controller_args = None
for dep in by_kind.get("Deployment", []):
    spec = dep["spec"]["template"]["spec"]
    for c in spec["containers"]:
        if c["name"] == "manager":
            controller_args = c.get("args", [])
        if c["name"] not in HARDENED:
            continue
        sc = c.get("securityContext", {})
        psc = spec.get("securityContext", {})
        where = f"{dep['metadata']['name']}/{c['name']}"
        if "command" in c:
            fail(f"{where}: command must not be set; the image entrypoint is used")
        if not (sc.get("runAsNonRoot") or psc.get("runAsNonRoot")):
            fail(f"{where}: runAsNonRoot is not set")
        uid = sc.get("runAsUser", psc.get("runAsUser"))
        want_uid = HARDENED[c["name"]]
        if want_uid is not None and uid != want_uid:
            fail(f"{where}: runAsUser is {uid}, want {want_uid}")
        if uid == 0:
            fail(f"{where}: runAsUser is 0")
        if c["name"] == "ui":
            if not any(m.get("mountPath") == "/tmp" for m in c.get("volumeMounts", [])):
                fail(f"{where}: /tmp must be a writable volume for the read-only root filesystem")
            env = {e["name"]: e.get("value") for e in c.get("env", [])}
            # nginx's runtime resolver ignores search domains.
            if ".svc." not in (env.get("SARTOR_API_URL") or ""):
                fail(f"{where}: SARTOR_API_URL must be a fully qualified Service URL, got {env.get('SARTOR_API_URL')}")
        if sc.get("readOnlyRootFilesystem") is not True:
            fail(f"{where}: readOnlyRootFilesystem is not true")
        if sc.get("allowPrivilegeEscalation") is not False:
            fail(f"{where}: allowPrivilegeEscalation is not false")
        if "ALL" not in sc.get("capabilities", {}).get("drop", []) or sc.get("capabilities", {}).get("add"):
            fail(f"{where}: capabilities must drop ALL and add none")
        seccomp = sc.get("seccompProfile", psc.get("seccompProfile", {}))
        if seccomp.get("type") != "RuntimeDefault":
            fail(f"{where}: seccompProfile is not RuntimeDefault")
        if not c.get("resources", {}).get("requests"):
            fail(f"{where}: resource requests are not set")

for kind in ("ClusterRole", "Role"):
    for role in by_kind.get(kind, []):
        name = role["metadata"]["name"]
        for r in rules_of(role):
            for key in ("apiGroups", "resources", "verbs"):
                if "*" in r.get(key, []):
                    fail(f"{kind} {name}: wildcard in {key}")
            if "" in r.get("apiGroups", []) and "secrets" in r.get("resources", []):
                if kind == "ClusterRole":
                    fail(f"ClusterRole {name}: grants Secrets cluster-wide")
                if set(r["verbs"]) != {"get"}:
                    fail(f"Role {name}: Secrets verbs must be exactly [get], got {r['verbs']}")

if controller_args is not None:
    for required in ("enable-oom-patching", "enable-git-writeback", "analysis-interval", "leader-elect"):
        if flag(controller_args, required) is None:
            fail(f"controller: --{required} is not passed")

    oom = flag(controller_args, "enable-oom-patching") == "true"
    crs = [r for r in by_kind.get("ClusterRole", []) if r["metadata"]["name"].endswith("-controller")]
    writes = any(
        grants(rule, "apps", res, ["update", "patch", "create", "delete"])
        for cr in crs
        for rule in rules_of(cr)
        for res in WORKLOADS
    )
    pods = any(grants(rule, "", "pods", ["get", "list", "watch"]) for cr in crs for rule in rules_of(cr))
    if writes != oom:
        fail(f"controller: workload write RBAC is {writes} but --enable-oom-patching={oom}")
    if pods != oom:
        fail(f"controller: pods read RBAC is {pods} but --enable-oom-patching={oom}")

    secure = flag(controller_args, "metrics-secure") == "true"
    auth = any(grants(rule, "authentication.k8s.io", "tokenreviews", ["create"]) for cr in crs for rule in rules_of(cr))
    if auth != secure:
        fail(f"controller: tokenreviews RBAC is {auth} but --metrics-secure={secure}")

    leader = flag(controller_args, "leader-elect") == "true"
    roles = [r for r in by_kind.get("Role", []) if r["metadata"]["name"].endswith("-controller")]
    leases = any(grants(rule, "coordination.k8s.io", "leases", ["update"]) for r in roles for rule in rules_of(r))
    if leases != leader:
        fail(f"controller: leases RBAC is {leases} but --leader-elect={leader}")

if errors:
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
print(f"ok: {len(docs)} objects, controller={'yes' if controller_args is not None else 'no'}")

#!/usr/bin/env python3
"""Safety rules for kubernetes/ and the K3s config example.

Every rule is one `check(condition, message)` line. Folders are rendered with
kustomize; nothing contacts a cluster. Run through scripts/validate-kubernetes.sh.

  python3 scripts/check-kubernetes.py                       # run the rules
  python3 scripts/check-kubernetes.py --export-schemas DIR  # CRD schemas for kubeconform
"""
import argparse
from functools import cache
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
SECRET_ENV = re.compile(r"PASSWORD|SECRET|TOKEN|CLIENT_URL")
failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


@cache
def render(path):
    out = subprocess.run(["kustomize", "build", str(ROOT / path)],
                         check=True, capture_output=True, text=True).stdout
    return tuple(d for d in yaml.safe_load_all(out) if d)


def folders(parent):
    return sorted(f"{parent}/{p.name}" for p in (ROOT / parent).iterdir() if p.is_dir())


def of_kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def label(doc):
    return f'{doc["kind"]}/{doc["metadata"]["name"]}'


def annotation(doc, key):
    return (doc["metadata"].get("annotations") or {}).get(key)


def pod_spec(doc):
    spec = doc["spec"]["jobTemplate"]["spec"] if doc["kind"] == "CronJob" else doc["spec"]
    return spec["template"]["spec"]


def check_k3s_configs():
    networks = []
    for example in sorted((ROOT / "provisioning/k3s").glob("*.yaml.example")):
        config = yaml.safe_load(example.read_text())
        where = f"K3s {example.name}"
        check({"traefik", "servicelb"} <= set(config["disable"]),
              f"{where}: keep Traefik and ServiceLB disabled so Caddy keeps 80/443")
        check(config.get("secrets-encryption") is True, f"{where}: encrypt Secrets at rest")
        check(not config.get("disable-network-policy"), f"{where}: keep NetworkPolicy enforcement")
        pods = ipaddress.ip_network(config["cluster-cidr"])
        services = ipaddress.ip_network(config["service-cidr"])
        check(ipaddress.ip_address(config["cluster-dns"]) in services,
              f"{where}: cluster DNS must be inside the Service CIDR")
        networks += [pods, services]
    for i, net in enumerate(networks):
        check(not any(net.overlaps(other) for other in networks[i + 1:]),
              f"K3s examples: {net} overlaps another cluster network")


def check_flux_root(cluster):
    docs = render(cluster)
    where = f"Flux {cluster}"
    instance = of_kind(docs, "FluxInstance")[0]
    spec = instance["spec"]
    check(annotation(instance, "kustomize.toolkit.fluxcd.io/prune") == "disabled",
          f"{where}: a bad commit must not be able to prune (uninstall) Flux")
    check(set(spec["components"]) == {"source-controller", "kustomize-controller"},
          f"{where}: install only the source and kustomize controllers")
    check(spec["sync"]["ref"] == "refs/heads/main" and spec["sync"]["path"] == f"./{cluster}",
          f"{where}: sync main from ./{cluster}")
    check(not of_kind(docs, "GitRepository"),
          f"{where}: only Flux Operator declares the root GitRepository")
    check([d["metadata"]["name"] for d in of_kind(docs, "Deployment")] == ["flux-operator"],
          f"{where}: Flux controllers come from the FluxInstance, not from Git")

    patch = {p["target"]["kind"]: yaml.safe_load(p["patch"]) for p in spec["kustomize"]["patches"]}
    flags = {op["value"] for op in patch["Deployment"]}
    check({"--no-cross-namespace-refs=true", "--no-remote-bases=true"} <= flags,
          f"{where}: keep the kustomize-controller restrictions")
    sparse = next(op["value"] for op in patch["GitRepository"] if op["path"] == "/spec/sparseCheckout")

    children = {k["metadata"]["name"]: k["spec"] for k in of_kind(docs, "Kustomization")}
    for name, child in children.items():
        path = child["path"].removeprefix("./")
        check(path in sparse, f"{where}: add {path} to the GitRepository sparseCheckout")
        check(child["prune"] is False and child["wait"] is True,
              f"{where}: Kustomization {name} must not prune and must wait for health")
        for dep in child.get("dependsOn", []):
            check(dep["name"] in children, f"{where}: {name} depends on unknown {dep['name']}")

    dashboard = next(p for p in of_kind(docs, "NetworkPolicy")
                     if p["spec"]["podSelector"].get("matchLabels", {}).get("app.kubernetes.io/name") == "flux-operator")
    check(dashboard["spec"].get("ingress") == [],
          f"{where}: the Mission Control dashboard takes no Pod ingress (port-forward or host Caddy only)")
    shared = patch["NetworkPolicy"][0]["value"]["matchExpressions"][0]["values"]
    check(set(shared) == {"source-controller", "kustomize-controller"},
          f"{where}: Flux's shared network allowances must not include the dashboard")


def check_pod(where, pod, long_running):
    check(not pod.get("hostNetwork"), f"{where}: no host networking")
    check(pod.get("automountServiceAccountToken") is False, f"{where}: no Kubernetes API token")
    check(pod.get("securityContext", {}).get("runAsNonRoot") is True, f"{where}: run as non-root")
    check(all("hostPath" not in v for v in pod.get("volumes", [])), f"{where}: no host directories")
    for c in pod["containers"]:
        sc = c.get("securityContext", {})
        check(DIGEST.search(c["image"]), f"{where}: pin {c['image']} by digest")
        check(sc.get("allowPrivilegeEscalation") is False and sc.get("readOnlyRootFilesystem") is True
              and sc.get("capabilities", {}).get("drop") == ["ALL"],
              f"{where}: container {c['name']} needs the restricted securityContext")
        check({"requests", "limits"} <= set(c.get("resources", {})),
              f"{where}: container {c['name']} needs resource requests and limits")
        check(all("hostPort" not in p for p in c.get("ports", [])), f"{where}: no host ports")
        if long_running:
            check("readinessProbe" in c and "livenessProbe" in c,
                  f"{where}: container {c['name']} needs readiness and liveness probes")
        for env in c.get("env", []):
            if SECRET_ENV.search(env["name"]):
                check("valueFrom" in env, f"{where}: {env['name']} must come from a Secret or ConfigMap")


def check_workload_folder(path):
    docs = render(path)
    namespaces = {d["metadata"]["namespace"] for d in docs if d["metadata"].get("namespace")}
    for ns in namespaces:
        check(any(p["metadata"]["name"] == "default-deny" and p["metadata"].get("namespace") == ns
                  and p["spec"]["podSelector"] == {}
                  and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"}
                  and not p["spec"].get("ingress") and not p["spec"].get("egress")
                  for p in of_kind(docs, "NetworkPolicy")),
              f"{path}: namespace {ns} needs a default-deny NetworkPolicy with no allow rules")
    for doc in docs:
        where = f"{path} {label(doc)}"
        check(doc["kind"] not in {"Secret", "Ingress"}, f"{where}: credentials and public routes stay out of Git")
        if doc["kind"] == "Service":
            check(doc["spec"].get("type", "ClusterIP") == "ClusterIP", f"{where}: Services stay ClusterIP")
        if doc["kind"] in {"Deployment", "StatefulSet", "CronJob"}:
            check_pod(where, pod_spec(doc), long_running=doc["kind"] != "CronJob")


def check_postgres(path="kubernetes/databases/postgres"):
    docs = render(path)
    for entry in sorted((ROOT / path / "app-databases").glob("*/kustomization.yaml")):
        docs += render(str(entry.parent.relative_to(ROOT)))
    cluster = of_kind(docs, "Cluster")[0]
    spec = cluster["spec"]
    check(spec["instances"] == 1, "PostgreSQL: one instance until a second storage node exists")
    check(annotation(cluster, "kustomize.toolkit.fluxcd.io/prune") == "disabled",
          "PostgreSQL: Flux must never prune the Cluster (that deletes its data volume)")
    check(DIGEST.search(spec["imageName"]), "PostgreSQL: pin imageName by digest")
    check(spec.get("enableSuperuserAccess") is not True, "PostgreSQL: no network superuser login")
    check({"requests", "limits"} <= set(spec.get("resources", {})), "PostgreSQL: set resource requests and limits")
    check(spec["affinity"]["nodeSelector"].get("homelab.example/local-storage") == "true",
          "PostgreSQL: pin the instance to the local-storage node")
    storage = {d["metadata"]["name"]: d for d in of_kind(docs, "StorageClass")}.get(spec["storage"]["storageClass"])
    check(storage is not None and storage["reclaimPolicy"] == "Retain"
          and storage["volumeBindingMode"] == "WaitForFirstConsumer",
          "PostgreSQL: its StorageClass must retain data on the node that holds it")

    roles = {r["spec"]["name"]: r["spec"] for r in of_kind(docs, "DatabaseRole")}
    for name, role in roles.items():
        check(role.get("passwordSecret", {}).get("name"), f"PostgreSQL role {name}: password comes from a Secret")
        check(not role.get("superuser") and not role.get("createrole") and not role.get("bypassrls"),
              f"PostgreSQL role {name}: app roles get no admin privileges")
        check(role.get("databaseRoleReclaimPolicy", "retain") == "retain",
              f"PostgreSQL role {name}: removing it from Git must not drop it")
    for database in of_kind(docs, "Database"):
        db = database["spec"]
        check(db["owner"] in roles, f"PostgreSQL database {db['name']}: owner {db['owner']} needs a DatabaseRole")
        check(db.get("databaseReclaimPolicy", "retain") == "retain",
              f"PostgreSQL database {db['name']}: removing it from Git must not drop it")


def check_infrastructure_images(path):
    for deployment in of_kind(render(path), "Deployment"):
        for c in pod_spec(deployment)["containers"]:
            check(DIGEST.search(c["image"]), f"{path} {label(deployment)}: pin {c['image']} by digest")


def check_twenty():
    docs = render("kubernetes/apps/twenty")
    workloads = of_kind(docs, "Deployment") + of_kind(docs, "StatefulSet")
    check({d["metadata"]["name"] for d in workloads} == {"twenty-server", "twenty-worker", "twenty-redis"}
          and all(d["spec"]["replicas"] == 1 for d in workloads)
          and not of_kind(docs, "Job") and not of_kind(docs, "CronJob"),
          "Twenty: run one server, worker and Redis replica")
    config = of_kind(docs, "ConfigMap")[0]["data"]
    check(config.get("IS_MULTIWORKSPACE_ENABLED") == "true"
          and config.get("IS_CONFIG_VARIABLES_IN_DB_ENABLED") == "false"
          and config.get("IS_WORKSPACE_CREATION_LIMITED_TO_SERVER_ADMINS") == "true",
          "Twenty: retain environment-controlled multi-workspace mode and restricted workspace creation")
    children = {d["metadata"]["name"]: d["spec"] for d in
                of_kind(render("kubernetes/clusters/public-edge"), "Kustomization")}
    check(children["twenty-database"].get("suspend") is False
          and children["twenty"].get("suspend") is False
          and "twenty-database" in {d["name"] for d in children["twenty"].get("dependsOn", [])},
          "Twenty: reconcile its database before starting the application")
    check(not any(d["metadata"]["name"] == "twenty" for d in
                  of_kind(render("kubernetes/databases/postgres"), "Database")),
          "Twenty: its database must have a separate Flux owner")
    database_docs = render("kubernetes/databases/postgres/app-databases/twenty")
    role = of_kind(database_docs, "DatabaseRole")[0]["spec"]
    database = of_kind(database_docs, "Database")[0]["spec"]
    check(role["name"] == database["name"] == database["owner"] == "twenty"
          and role.get("passwordSecret", {}).get("name") == "twenty-db-password"
          and role.get("login") is True,
          "Twenty: use its own login, password Secret and database")
    check(all(role.get(flag) is False for flag in
              ("superuser", "createdb", "createrole", "replication", "bypassrls", "inherit"))
          and role.get("inRoles") == [],
          "Twenty: no cluster privileges or memberships in other database roles")
    cluster = of_kind(render("kubernetes/databases/postgres"), "Cluster")[0]
    check(cluster["spec"]["postgresql"].get("pg_hba", [])[:2] == [
              "host twenty twenty all scram-sha-256", "host all twenty all reject"],
          "Twenty: authenticate only to its database, rejecting other database connections first")
    for deployment in of_kind(docs, "Deployment"):
        for container in pod_spec(deployment)["containers"]:
            sources = container.get("envFrom", [])
            check([s["secretRef"]["name"] for s in sources if "secretRef" in s]
                  == ["twenty-credentials"],
                  f"Twenty {label(deployment)}: use only its own application credentials Secret")


def check_no_unresolved_variables(path):
    for doc in render(path):
        if annotation(doc, "kustomize.toolkit.fluxcd.io/substitute") == "disabled":
            continue
        text = yaml.safe_dump(doc).replace("${GIT_REPOSITORY_URL}", "")
        check("${" not in text, f"{path} {label(doc)}: unresolved ${{...}} variable")


def export_schemas(directory):
    for path in ["kubernetes/schemas/flux-2.10.0-crds.yaml",
                 "kubernetes/infrastructure/flux-operator/operator.yaml",
                 "kubernetes/infrastructure/cloudnative-pg/operator.yaml"]:
        for doc in yaml.safe_load_all((ROOT / path).read_text()):
            if doc and doc["kind"] == "CustomResourceDefinition":
                for version in doc["spec"]["versions"]:
                    name = f'{doc["spec"]["names"]["kind"].lower()}_{version["name"]}.json'
                    (directory / name).write_text(json.dumps(version["schema"]["openAPIV3Schema"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-schemas", type=Path, metavar="DIR")
    args = parser.parse_args()
    if args.export_schemas:
        export_schemas(args.export_schemas)
        return

    clusters = folders("kubernetes/clusters")
    workloads = folders("kubernetes/apps") + folders("kubernetes/databases") + folders("kubernetes/storage")
    check_k3s_configs()
    for cluster in clusters:
        check_flux_root(cluster)
    for path in workloads:
        check_workload_folder(path)
    check_postgres()
    check_twenty()
    for path in folders("kubernetes/infrastructure"):
        check_infrastructure_images(path)
    for entry in sorted((ROOT / "kubernetes").rglob("kustomization.yaml")):
        check_no_unresolved_variables(str(entry.parent.relative_to(ROOT)))

    if failures:
        print("\n".join(f"Kubernetes rule failed: {f}" for f in failures), file=sys.stderr)
        sys.exit(1)
    print("Kubernetes rules passed.")


if __name__ == "__main__":
    main()

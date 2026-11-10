#!/usr/bin/env python3
"""Check the public-only pilot's trust/exposure/data contracts on rendered YAML."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"Kubernetes contract: {message}")


def render(path):
    result = subprocess.run(
        ["kustomize", "build", str(ROOT / path)],
        check=True, capture_output=True, text=True,
    )
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas", type=Path)
    args = parser.parse_args()
    if args.schemas:
        # Exact pinned Flux and operator schemas; neither requires a cluster.
        for path in ["kubernetes/schemas/flux-2.10.0-crds.yaml",
                     "kubernetes/infrastructure/flux-operator/operator.yaml"]:
            for doc in yaml.safe_load_all((ROOT / path).read_text()):
                if doc and doc["kind"] == "CustomResourceDefinition":
                    for version in doc["spec"]["versions"]:
                        name = f'{doc["spec"]["names"]["kind"].lower()}_{version["name"]}.json'
                        (args.schemas / name).write_text(json.dumps(version["schema"]["openAPIV3Schema"]))
        return

    require({p.name for p in (ROOT / "kubernetes/clusters").iterdir() if p.is_dir()} == {"public-edge"},
            "This initial change must bootstrap only the public cluster")
    require({p.name for p in (ROOT / "kubernetes/apps").iterdir() if p.is_dir()} == {"kaneo"},
            "This initial change adds only Kaneo and its database")
    require({p.name for p in (ROOT / "kubernetes/databases").iterdir() if p.is_dir()} == {"postgres"},
            "Only the pilot's PostgreSQL database is in scope")
    require({p.name for p in (ROOT / "provisioning/k3s").glob("*.yaml.example")} == {"public-edge.yaml.example"},
            "Private-cluster provisioning belongs to a later change")
    for cluster, app in [("public-edge", "kaneo")]:
        docs = render(f"kubernetes/clusters/{cluster}")
        instances = [d for d in docs if d["kind"] == "FluxInstance"]
        require(len(instances) == 1 and instances[0]["metadata"]["name"] == "flux",
                "One public FluxInstance must own the Flux installation")
        instance = instances[0]
        spec = instance["spec"]
        require(instance["metadata"]["annotations"].get("kustomize.toolkit.fluxcd.io/prune") == "disabled",
                "Accidental root pruning must not uninstall Flux")
        require(spec["distribution"] == {"version": "2.10.0", "registry": "ghcr.io/fluxcd"} and
                set(spec["components"]) == {"source-controller", "kustomize-controller"},
                "Pin Flux and install only the selected two controllers")
        require(spec["sync"] == {
            "name": "flux-system", "kind": "GitRepository", "url": "${GIT_REPOSITORY_URL}",
            "ref": "refs/heads/main", "path": f"./kubernetes/clusters/{cluster}",
            "pullSecret": "flux-system", "interval": "1m",
        }, "Operator-generated sync must use main, the public root and this cluster's external identity/URL")
        patches = {p["target"]["kind"]: yaml.safe_load(p["patch"]) for p in spec["kustomize"]["patches"]}
        source_patch = {p["path"]: p.get("value") for p in patches["GitRepository"]}
        require(set(source_patch["/spec/sparseCheckout"]) == {
            f"kubernetes/clusters/{cluster}", f"kubernetes/apps/{app}",
            "kubernetes/databases/postgres", "kubernetes/infrastructure/flux-operator",
        }, "Source must include only this cluster's paths and operator")
        require({p["value"] for p in patches["Deployment"]} >= {
            "--no-cross-namespace-refs=true", "--no-remote-bases=true",
            "--feature-gates=StrictPostBuildSubstitutions=true",
        }, "Keep the controller restrictions when transferring ownership")
        root_patch = {p["path"]: p.get("value") for p in patches["Kustomization"]}
        require(root_patch["/spec/postBuild"] == {
            "substituteFrom": [{"kind": "Secret", "name": "cluster-settings"}],
        }, "Root sync must substitute the operator's external Git URL on subsequent reconciliations")
        require(not any(d["kind"] == "GitRepository" for d in docs),
                "Only Flux Operator may declare the root source")
        require(not any(d["kind"] == "Namespace" for d in docs),
                "Bootstrap the namespace separately; only Flux Operator may inventory it")
        syncs = {d["metadata"]["name"]: d for d in docs
                 if d["kind"] == "Kustomization"}
        require(set(syncs) == {"postgres", app}, "Only the operator may declare the root reconciliation")
        require(syncs[app]["spec"]["path"] == f"./kubernetes/apps/{app}", "Wrong workload path")
        require(syncs["postgres"]["spec"]["path"] == "./kubernetes/databases/postgres",
                "PostgreSQL must reconcile independently from the application")
        require(syncs[app]["spec"].get("dependsOn") == [{"name": "postgres"}],
                "Kaneo must wait for PostgreSQL reconciliation")
        require(syncs["postgres"]["spec"].get("wait") is True and
                syncs["postgres"]["spec"].get("suspend", False) is False and
                syncs["postgres"]["spec"]["prune"] is False,
                "Database reconciliation must check health and protect state")
        if app == "kaneo":
            require(syncs[app]["spec"].get("suspend", False) is False,
                    "Prepared public bootstrap must reconcile Kaneo")
            require(syncs[app]["spec"]["prune"] is False, "Kaneo state must not be pruned")
        for name in ["postgres", app]:
            require(syncs[name]["spec"]["decryption"] == {
                "provider": "sops", "secretRef": {"name": "sops-age"},
            }, "Preserve built-in SOPS/age support for both workloads")

        deployments = [d for d in docs if d["kind"] == "Deployment"]
        require([d["metadata"]["name"] for d in deployments] == ["flux-operator"],
                "Git must manage the operator, not a second copy of its Flux controllers")
        operator = deployments[0]["spec"]["template"]["spec"]
        container = operator["containers"][0]
        require(container["image"] == "ghcr.io/controlplaneio-fluxcd/flux-operator:v0.62.1@sha256:1d09a3f96524d0895fb11e8072d84a3dd029cecdec0f869efa371be6b001469c",
                "Pin the multiarchitecture operator image")
        require({e["name"]: e.get("value") for e in container["env"]}["WEB_SERVER_PORT"] == "9080" and
                "--web-server-only=true" not in container["args"],
                "The supported operator must run its embedded dashboard")
        require(not operator.get("hostNetwork") and
                all("hostPort" not in p for p in container["ports"]),
                "The dashboard must not bind host ports")
        require(not any(d["kind"] in {"Ingress", "HTTPRoute", "Secret", "HelmRelease"} for d in docs),
                "No public dashboard, credentials or extra Helm controller in the pilot root")
        require(all(d["spec"].get("type", "ClusterIP") == "ClusterIP" for d in docs if d["kind"] == "Service"),
                "Operator Services must remain internal")
        values = yaml.safe_load((ROOT / "kubernetes/infrastructure/flux-operator/values.yaml").read_text())
        require(values["web"]["config"] == {} and values["web"]["networkPolicy"]["create"] is False,
                "Keep the default read-only dashboard and replace the broad chart ingress allowance")
        policy = next(d for d in docs if d["kind"] == "NetworkPolicy")
        require(policy["spec"] == {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "flux-operator", "app.kubernetes.io/instance": "flux-operator"}},
            "policyTypes": ["Ingress"], "ingress": [],
        }, "Dashboard Pod ingress must be denied; access uses API port-forward")
        require(patches["NetworkPolicy"] == [{
            "op": "replace", "path": "/spec/podSelector", "value": {
                "matchExpressions": [{"key": "app", "operator": "In",
                                      "values": ["source-controller", "kustomize-controller"]}],
            },
        }], "Shared Flux allowances must not reopen dashboard ingress")

    all_networks = []
    for example in (ROOT / "provisioning/k3s").glob("*.yaml.example"):
        config = yaml.safe_load(example.read_text())
        require(set(config["disable"]) >= {"traefik", "servicelb"}, "K3s must preserve Compose ingress ports")
        require(config.get("secrets-encryption") is True, "Enable datastore secrets encryption")
        require(not config.get("disable-network-policy", False), "Keep NetworkPolicy enforcement")
        pod_net = ipaddress.ip_network(config["cluster-cidr"])
        service_net = ipaddress.ip_network(config["service-cidr"])
        require(ipaddress.ip_address(config["cluster-dns"]) in service_net, "DNS must be inside Service CIDR")
        all_networks.extend([pod_net, service_net])
    for i, net in enumerate(all_networks):
        require(all(not net.overlaps(other) for other in all_networks[i + 1:]), "Example cluster networks overlap")

    for app in ["kaneo"]:
        app_docs = render(f"kubernetes/apps/{app}")
        database_docs = render("kubernetes/databases/postgres")
        require([(d["kind"], d["metadata"]["name"]) for d in app_docs
                 if d["kind"] in {"Deployment", "StatefulSet"}] == [("Deployment", "kaneo")],
                "The app path must not own PostgreSQL")
        require([(d["kind"], d["metadata"]["name"]) for d in database_docs
                 if d["kind"] in {"Deployment", "StatefulSet"}] == [("StatefulSet", "kaneo-postgres")],
                "The database path must own only its StatefulSet")
        docs = app_docs + database_docs
        identities = [(d["apiVersion"], d["kind"], d["metadata"].get("namespace"), d["metadata"]["name"]) for d in docs]
        require(len(set(identities)) == len(identities), "App/database Flux inventories must not share resources")
        require(all(d["metadata"].get("namespace") == "kaneo" for d in docs
                    if d["kind"] in {"Deployment", "StatefulSet", "Service", "NetworkPolicy"}),
                "Keep pilot resource identities in their existing namespace")
        workloads = {(d["kind"], d["metadata"]["name"]): d for d in docs
                     if d["kind"] in {"Deployment", "StatefulSet"}}
        require(set(workloads) == {("Deployment", "kaneo"), ("StatefulSet", "kaneo-postgres")},
                "Kaneo must be a Deployment and PostgreSQL a StatefulSet")
        require(not any(d["kind"] == "PersistentVolumeClaim" for d in docs),
                "Only PostgreSQL's StatefulSet may allocate persistent storage")
        kaneo = workloads[("Deployment", "kaneo")]["spec"]["template"]["spec"]
        require({v["name"] for v in kaneo["volumes"]} == {"runtime", "tmp"} and
                all("emptyDir" in v for v in kaneo["volumes"]),
                "Kaneo needs only ephemeral runtime and temporary storage")
        require({m["mountPath"] for m in kaneo["containers"][0]["volumeMounts"]} ==
                {"/var/lib/kaneo", "/tmp"}, "Kaneo must have no application-data mount")
        postgres = workloads[("StatefulSet", "kaneo-postgres")]["spec"]
        require(postgres["replicas"] == 1, "PostgreSQL must remain a single database writer")
        require(postgres["persistentVolumeClaimRetentionPolicy"] == {
            "whenDeleted": "Retain", "whenScaled": "Retain",
        }, "Retain PostgreSQL claims on StatefulSet deletion and scale-down")
        claims = postgres["volumeClaimTemplates"]
        require(len(claims) == 1 and claims[0]["metadata"]["name"] == "data",
                "PostgreSQL needs one stable data claim per database Pod")
        claim = claims[0]
        require(claim["metadata"]["annotations"].get("kustomize.toolkit.fluxcd.io/prune") == "disabled" and
                claim["spec"] == {
                    "accessModes": ["ReadWriteOnce"], "storageClassName": "kaneo-local-retain",
                    "resources": {"requests": {"storage": "10Gi"}},
                }, "Protect the PostgreSQL data claim and retain its storage contract")
        pg_container = postgres["template"]["spec"]["containers"][0]
        require(pg_container["image"].startswith("postgres:18.") and
                {e["name"]: e.get("value") for e in pg_container["env"]}["PGDATA"] == "/var/lib/postgresql/18/docker" and
                {m["name"]: m["mountPath"] for m in pg_container["volumeMounts"]}["data"] == "/var/lib/postgresql",
                "Use PostgreSQL 18 with its supported major-version data layout")
        services = {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "Service"}
        require(postgres["serviceName"] == "kaneo-postgres-headless" and
                services["kaneo-postgres-headless"]["clusterIP"] == "None" and
                services["kaneo-postgres-headless"]["selector"] == postgres["selector"]["matchLabels"],
                "PostgreSQL needs a matching governing headless Service")
        require(services["kaneo-postgres"].get("clusterIP") != "None" and
                services["kaneo-postgres"]["selector"] == postgres["selector"]["matchLabels"],
                "Keep Kaneo's database connection on its existing ClusterIP Service")
        deny = [d for d in docs if d["kind"] == "NetworkPolicy" and
                d["spec"].get("podSelector") == {} and
                set(d["spec"]["policyTypes"]) == {"Ingress", "Egress"} and
                not d["spec"].get("ingress") and not d["spec"].get("egress")]
        require(deny, f"{app} needs default-deny ingress and egress")
        for doc in docs:
            kind, spec = doc["kind"], doc.get("spec", {})
            require(kind not in {"Ingress", "Secret"}, f"{app}: exposure/secrets must be explicit operator inputs")
            if kind == "Service":
                require(spec.get("type", "ClusterIP") == "ClusterIP", f"{app} must have no external Service listener")
            if kind == "StorageClass":
                require(doc["reclaimPolicy"] == "Retain" and doc["volumeBindingMode"] == "WaitForFirstConsumer", "Keep data on the selected node")
            if kind not in {"Deployment", "StatefulSet"}:
                continue
            pod = spec["template"]["spec"]
            require(not pod.get("hostNetwork") and pod.get("automountServiceAccountToken") is False, "Pilot cannot access host networking or Kubernetes credentials")
            require(pod["securityContext"]["runAsNonRoot"] is True, "Pilot must run without root")
            if app == "kaneo":
                require(spec["replicas"] == 1, "Keep pilot workloads at one replica")
                require(pod["nodeSelector"] == {
                    "homelab.example/cluster": "public-edge",
                    "homelab.example/kaneo-storage": "true",
                }, "Pin Kaneo and its database to the public storage owner")
            for volume in pod.get("volumes", []):
                require("hostPath" not in volume, "No host mounts in pilots")
            for container in pod["containers"]:
                require(re.search(r"@sha256:[0-9a-f]{64}$", container["image"]), "Pin workload images to index digests")
                sc = container["securityContext"]
                require(sc["allowPrivilegeEscalation"] is False and sc["readOnlyRootFilesystem"] is True and sc["capabilities"]["drop"] == ["ALL"], "Restricted runtime required")
                require("requests" in container["resources"] and "limits" in container["resources"], "Bound pilot resource usage")
                require("readinessProbe" in container and "livenessProbe" in container, "Health probes required")
                require(all("hostPort" not in port for port in container.get("ports", [])), "No host ports")
                for env in container.get("env", []):
                    if re.search("PASSWORD|SECRET|CLIENT_URL", env["name"]):
                        require("valueFrom" in env, "Credentials and deployment URLs must stay outside Git")
    print("Public-only Kubernetes scope, pilot exposure, runtime and storage contracts passed.")


if __name__ == "__main__":
    main()

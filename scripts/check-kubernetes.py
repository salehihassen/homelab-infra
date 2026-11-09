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
        # Use the exact vendored Flux CRD schemas, not a moving external branch.
        for doc in yaml.safe_load_all((ROOT / "kubernetes/infrastructure/flux-system/gotk-components.yaml").read_text()):
            if doc and doc["kind"] == "CustomResourceDefinition":
                for version in doc["spec"]["versions"]:
                    name = f'{doc["spec"]["names"]["kind"].lower()}_{version["name"]}.json'
                    (args.schemas / name).write_text(json.dumps(version["schema"]["openAPIV3Schema"]))
        return

    require({p.name for p in (ROOT / "kubernetes/clusters").iterdir() if p.is_dir()} == {"public-edge"},
            "This initial change must bootstrap only the public cluster")
    require({p.name for p in (ROOT / "kubernetes/apps").iterdir() if p.is_dir()} == {"kaneo"},
            "This initial change adds only Kaneo and its database")
    require({p.name for p in (ROOT / "provisioning/k3s").glob("*.yaml.example")} == {"public-edge.yaml.example"},
            "Private-cluster provisioning belongs to a later change")
    for cluster, app in [("public-edge", "kaneo")]:
        docs = render(f"kubernetes/clusters/{cluster}")
        source = next(d for d in docs if d["kind"] == "GitRepository")
        require(source["spec"]["secretRef"]["name"] == "flux-system", "Git must use this cluster's deploy identity")
        require(source["spec"]["url"] == "${GIT_REPOSITORY_URL}", "Git URL must remain an external bootstrap input")
        require(source["spec"]["ref"] == {"branch": "main"}, "Deploy only the protected main branch")
        require(set(source["spec"]["sparseCheckout"]) == {
            f"kubernetes/clusters/{cluster}", f"kubernetes/apps/{app}",
            "kubernetes/infrastructure/flux-system",
        }, "Source must include only this cluster's paths and shared controllers")
        syncs = {d["metadata"]["name"]: d for d in docs
                 if d["kind"] == "Kustomization"}
        require(set(syncs) == {"flux-system", app}, "Each cluster reconciles only its own pilot")
        require(syncs["flux-system"]["spec"]["path"] == f"./kubernetes/clusters/{cluster}", "Wrong cluster root")
        require(syncs[app]["spec"]["path"] == f"./kubernetes/apps/{app}", "Wrong workload path")
        if app == "kaneo":
            require(syncs[app]["spec"].get("suspend", False) is False,
                    "Prepared public bootstrap must reconcile Kaneo")
            require(syncs[app]["spec"]["prune"] is False, "Kaneo state must not be pruned")

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
        docs = render(f"kubernetes/apps/{app}")
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
            if kind == "PersistentVolumeClaim":
                require(doc["metadata"]["annotations"].get("kustomize.toolkit.fluxcd.io/prune") == "disabled", "Protect PVCs from pruning")
            if kind == "StorageClass":
                require(doc["reclaimPolicy"] == "Retain" and doc["volumeBindingMode"] == "WaitForFirstConsumer", "Keep data on the selected node")
            if kind != "Deployment":
                continue
            pod = spec["template"]["spec"]
            require(not pod.get("hostNetwork") and pod.get("automountServiceAccountToken") is False, "Pilot cannot access host networking or Kubernetes credentials")
            require(pod["securityContext"]["runAsNonRoot"] is True, "Pilot must run without root")
            if app == "kaneo":
                require(spec["replicas"] == 1 and spec["strategy"]["type"] == "Recreate", "Stateful pilot must have one writer")
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

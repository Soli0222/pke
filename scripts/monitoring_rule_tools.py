"""監視 chart の描画と、実 Alloy によるローカルの cluster scope 変換。"""

import json
import re
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOY = "grafana/alloy:v1.19.2"
# Mimir 3.2.1 の mimir-prometheus e4534561e9ed の VERSION と一致。
PROMETHEUS = "prom/prometheus:v3.13.0"


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def production_values(cluster):
    path = (
        ROOT
        / f"flux/clusters/{cluster}/apps/monitoring-rules/helmrelease-monitoring-rules.yaml"
    )
    return yaml.safe_load(path.read_text())["spec"]["values"]


def render(values):
    with tempfile.TemporaryDirectory(prefix="pke-render-") as tmp:
        path = Path(tmp) / "values.yaml"
        path.write_text(yaml.safe_dump(values))
        docs = yaml.safe_load_all(
            run(
                "helm",
                "template",
                "monitoring-rules",
                "charts/monitoring-rules",
                "-n",
                "monitoring-rules",
                "-f",
                str(path),
            )
        )
        return [g for d in docs if d for g in d["spec"]["groups"]]


def scope(groups_by_cluster, out):
    """偽 Kubernetes/Mimir API のみを使い、本番と同じ Alloy の変換結果を返す。"""
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "test", "resourceVersion": "1"},
    }
    resource = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {
            "namespace": "test",
            "name": "catalog",
            "uid": "00000000-0000-0000-0000-000000000741",
            "resourceVersion": "1",
        },
    }
    stored = {}
    lock = threading.Lock()
    stopping = threading.Event()

    class API(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, data, status=200, kind="application/json"):
            payload = (
                yaml.safe_dump(data) if kind == "application/yaml" else json.dumps(data)
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlsplit(self.path).path
            if "watch=true" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if "sendInitialEvents=true" in self.path:
                    obj = namespace if path == "/api/v1/namespaces" else resource
                    bookmark = {
                        "apiVersion": obj["apiVersion"],
                        "kind": obj["kind"],
                        "metadata": {
                            "resourceVersion": "1",
                            "annotations": {"k8s.io/initial-events-end": "true"},
                        },
                    }
                    for event in [
                        {"type": "ADDED", "object": obj},
                        {"type": "BOOKMARK", "object": bookmark},
                    ]:
                        self.wfile.write(json.dumps(event).encode() + b"\n")
                    self.wfile.flush()
                stopping.wait(5)
            elif path == "/api/v1/namespaces":
                self.reply(
                    {
                        "apiVersion": "v1",
                        "kind": "NamespaceList",
                        "metadata": {"resourceVersion": "1"},
                        "items": [namespace],
                    }
                )
            elif path == "/apis/monitoring.coreos.com/v1/prometheusrules":
                self.reply(
                    {
                        "apiVersion": "monitoring.coreos.com/v1",
                        "kind": "PrometheusRuleList",
                        "metadata": {"resourceVersion": "1"},
                        "items": [resource],
                    }
                )
            elif path == "/prometheus/config/v1/rules":
                with lock:
                    self.reply(
                        {k: list(v.values()) for k, v in stored.items()},
                        kind="application/yaml",
                    )
            else:
                self.reply({"path": path}, 404)

        def do_POST(self):
            assert self.headers.get("X-Scope-OrgID") == "anonymous"
            name = unquote(self.path.removeprefix("/prometheus/config/v1/rules/"))
            group = yaml.safe_load(self.rfile.read(int(self.headers["Content-Length"])))
            with lock:
                stored.setdefault(name, {})[group["name"]] = group
            self.reply({}, 202)

    server = ThreadingHTTPServer(("0.0.0.0", 0), API)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://host.docker.internal:{server.server_port}"
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o755)
    config_path = out / "fake-kubeconfig"
    config_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "test",
                "clusters": [{"name": "test", "cluster": {"server": endpoint}}],
                "contexts": [
                    {"name": "test", "context": {"cluster": "test", "user": "test"}}
                ],
                "users": [{"name": "test", "user": {}}],
            }
        )
    )
    result = {}
    containers = []
    try:
        for cluster, groups in groups_by_cluster.items():
            resource["spec"] = {"groups": groups}
            source = yaml.safe_load(
                (
                    ROOT / f"flux/clusters/{cluster}/apps/alloy/alloy-config.yaml"
                ).read_text()
            )["data"]["config.alloy"]
            start = source.index('mimir.rules.kubernetes "default" {')
            end = source.index("\n}\n", start)
            config = re.sub(
                r'address\s*=\s*"[^"]+"',
                f'address = "{endpoint}"',
                source[start : end + 3],
            )
            config = re.sub(r"\n  tls_config \{.*?\n  \}", "", config, flags=re.S)
            path = out / f"{cluster}.alloy"
            path.write_text(config)
            cid = run(
                "docker",
                "run",
                "-d",
                "--add-host=host.docker.internal:host-gateway",
                "-e",
                "KUBECONFIG=/work/fake-kubeconfig",
                "-v",
                f"{out}:/work:ro",
                ALLOY,
                "run",
                f"/work/{path.name}",
            )
            containers.append(cid)
            ns = f"{cluster}/test/catalog/" + resource["metadata"]["uid"]
            for _ in range(100):
                with lock:
                    ready = len(stored.get(ns, {})) == len(groups)
                if ready:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(run("docker", "logs", cid))
            run("docker", "rm", "-f", cid)
            containers.remove(cid)
            result[cluster] = list(stored[ns].values())
            for group in result[cluster]:
                for rule in group["rules"]:
                    assert rule["labels"]["cluster"] == cluster, rule
                    # 空の opt-in グループ以外は入力 selector に matcher が追加される。
                    assert (
                        f'cluster="{cluster}"' in rule["expr"]
                        or rule["expr"] == "vector(0) > 1"
                    ), rule
        return result
    finally:
        for cid in containers:
            run("docker", "rm", "-f", cid)
        stopping.set()
        server.shutdown()
        server.server_close()

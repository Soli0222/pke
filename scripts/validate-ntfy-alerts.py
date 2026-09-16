#!/usr/bin/env python3
"""実 Secret を読まず、render、route、ntfy の ACL/template/rotation を検証する。"""
import json
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
NTFY = "binwiederhier/ntfy:v2.28.0"
ALLOY = "grafana/alloy:v1.19.2"
AMTOOL = "prom/alertmanager:v0.31.1"
TOKENS = {name: "tk_" + char * 29 for name, char in
          [("admin", "a"), ("publisher", "b"), ("rotated", "d")]}
# 公式設定例の bcrypt hash。ローカル検証専用で本番には使用しない。
HASH = "$2a$10$YLiO8U21sX1uhZamTLJXHuxgVC0Z/GKISibrKCLohPgtG7yIxSk4C"


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def request(base, path, token=None, payload=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = json.dumps(payload).encode() if payload is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(base + path, data=data, headers=headers), timeout=5) as r:
            return r.status, r.read().decode()
    except HTTPError as e:
        return e.code, e.read().decode()


def main():
    with tempfile.TemporaryDirectory(prefix="pke-ntfy-validation-") as temp:
        out = Path(temp)
        out.chmod(0o755)  # amtool の非 root ユーザーにもダミー設定を公開する。
        hr = yaml.safe_load((ROOT / "flux/clusters/natsume/apps/ntfy/helmrelease-ntfy.yaml").read_text())
        (out / "values.yaml").write_text(yaml.safe_dump(hr["spec"]["values"]))
        print(run("helm", "lint", "charts/ntfy", "-f", str(out / "values.yaml")))
        rendered = run("helm", "template", "ntfy", "charts/ntfy", "-n", "ntfy", "-f", str(out / "values.yaml"))
        docs = list(yaml.safe_load_all(rendered))
        deployment = next(d for d in docs if d and d["kind"] == "Deployment")
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        for key in ("NTFY_AUTH_USERS", "NTFY_AUTH_TOKENS"):
            entry = next(e for e in env if e["name"] == key)
            assert "value" not in entry and "secretKeyRef" in entry["valueFrom"]
        assert not any(d and d["kind"] == "Secret" for d in docs)
        config = yaml.safe_load(next(d for d in docs if d and d["kind"] == "ConfigMap")["data"]["server.yml"])
        source = yaml.safe_load((ROOT / "flux/clusters/natsume/apps/alloy/alloy-config.yaml").read_text())["data"]["config.alloy"]
        (out / "config.alloy").write_text(source)
        alertmanager = source.split("global_config = `", 1)[1].split("`\n  alertmanagerconfig_selector", 1)[0]
        alertmanager = alertmanager.replace('` + remote.kubernetes.secret.slack_webhook.data["api-url"] + `', "https://slack.invalid/services/dummy")
        alertmanager = alertmanager.replace('` + remote.kubernetes.secret.ntfy_publish.data["token"] + `', TOKENS["publisher"])
        assert "`" not in alertmanager
        parsed = yaml.safe_load(alertmanager)
        assert set(parsed["route"]["group_by"]) == {"alertname", "severity", "pke_cluster"}
        (out / "alertmanager.yaml").write_text(alertmanager)
        mount = ["--network", "none", "-v", f"{out}:/work:ro"]
        run("docker", "run", "--rm", *mount, ALLOY, "validate", "--stability.level=experimental", "/work/config.alloy")
        def amtool(*args):
            return run("docker", "run", "--rm", *mount, "--entrypoint", "/bin/amtool", AMTOOL, *args)
        print(amtool("check-config", "/work/alertmanager.yaml"))
        for cluster, receiver in [("natsume", "natsume"), ("meruto", "meruto"), ("pke", "pke"), ("unknown", "pke"), ("", "pke"), (None, "pke")]:
            labels = ["alertname=Validation", "severity=info"]
            if cluster is not None:
                labels.append("pke_cluster=" + cluster)
            amtool("config", "routes", "test", "--config.file=/work/alertmanager.yaml",
                   "--verify.receivers=slack_webhook,ntfy_" + receiver, *labels)
        for cluster in ("natsume", "meruto"):
            run("kubectl", "kustomize", "flux/clusters/" + cluster)
        print("render / Alloy syntax / six routing cases / both kustomizations: OK")

        # 外部 upstream と添付機能を外したローカル ntfy。ACL は render 結果をそのまま使用。
        local = {k: config[k] for k in ("auth-default-access", "auth-access", "enable-login", "enable-signup", "require-login")}
        local.update({"base-url": "http://localhost", "listen-http": ":80",
                      "auth-file": "/data/user.db", "cache-file": "/data/cache.db"})
        (out / "ntfy.yaml").write_text(yaml.safe_dump(local))
        users = ",".join(f"{name}:{HASH}:{role}" for name, role in
                         [("soli", "admin"), ("alertmanager", "user")])
        volume = out.name
        run("docker", "volume", "create", volume)
        def start(publisher):
            tokens = f"soli:{TOKENS['admin']},alertmanager:{publisher}"
            cid = run("docker", "run", "-d", "--user", "0:0", "-p", "127.0.0.1::80",
                      "-v", f"{out}:/work:ro", "-v", f"{volume}:/data", "-e", "NTFY_AUTH_USERS=" + users,
                      "-e", "NTFY_AUTH_TOKENS=" + tokens, NTFY, "serve", "--config", "/work/ntfy.yaml")
            try:
                port = run("docker", "port", cid, "80/tcp").rsplit(":", 1)[1]
                base = "http://127.0.0.1:" + port
                for _ in range(50):
                    try:
                        if request(base, "/v1/health")[0] == 200:
                            return cid, base
                    except (URLError, ConnectionError):
                        pass
                    time.sleep(0.1)
                raise RuntimeError("local ntfy did not start")
            except BaseException:
                print(run("docker", "logs", cid))
                run("docker", "rm", "-f", cid)
                raise
        try:
            cid, base = start(TOKENS["publisher"])
            try:
                for topic in ("natsume-alerts", "meruto-alerts", "pke-alerts"):
                    for state in ("firing", "resolved"):
                        payload = {"version": "4", "status": state, "receiver": "validation",
                                   "groupLabels": {"alertname": "Validation"}, "commonLabels": {"alertname": "Validation", "severity": "info"},
                                   "commonAnnotations": {"summary": "Local notification validation"},
                                   "alerts": [{"status": state, "labels": {"alertname": "Validation"},
                                               "annotations": {"summary": "Local notification validation"}}]}
                        code, body = request(base, f"/{topic}?template=alertmanager", TOKENS["publisher"], payload)
                        assert code == 200, (topic, state, code)
                        message = json.loads(body)
                        assert state.upper() in message["message"].upper(), message
                        assert "Validation" in message["title"], message
                        assert "Local notification validation" in message["message"], message
                    assert request(base, f"/{topic}/json?poll=1", TOKENS["publisher"])[0] == 403
                    assert request(base, f"/{topic}/json?poll=1")[0] in (401, 403)
                assert request(base, "/other-topic", TOKENS["publisher"], {"message": "denied"})[0] == 403
                assert request(base, "/pke-alerts", "tk_" + "z" * 29, {"message": "denied"})[0] == 401
                assert request(base, "/other-topic", TOKENS["admin"], {"message": "admin retained"})[0] == 200
            finally:
                run("docker", "rm", "-f", cid)
            cid, base = start(TOKENS["rotated"])
            try:
                assert request(base, "/pke-alerts", TOKENS["publisher"], {"message": "old token"})[0] == 401
                assert request(base, "/pke-alerts", TOKENS["rotated"], {"message": "new token"})[0] == 200
                assert request(base, "/other-topic/json?poll=1", TOKENS["admin"])[0] == 200
            finally:
                run("docker", "rm", "-f", cid)
        finally:
            run("docker", "volume", "rm", volume)
        print("local ntfy: three topics firing/resolved, ACL, invalid token, rotation, retained admin: OK")


if __name__ == "__main__":
    main()

"""ローカル Alertmanager で実際の inhibition を検証する。配送先は loopback の閉ポート。"""

import copy
import datetime
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml
from monitoring_rule_tools import run


def validate_inhibition(config, out, image):
    local = copy.deepcopy(config)
    for receiver in local["receivers"]:
        receiver["webhook_configs"] = [
            {"url": "http://127.0.0.1:9/unused", "send_resolved": True}
        ]
    path = out / "inhibition.yaml"
    path.write_text(yaml.safe_dump(local))
    cid = run(
        "docker",
        "run",
        "-d",
        "-p",
        "127.0.0.1::9093",
        "-v",
        f"{out}:/work:ro",
        image,
        "--config.file=/work/inhibition.yaml",
        "--storage.path=/tmp/alertmanager",
        "--cluster.listen-address=",
    )
    try:
        port = run("docker", "port", cid, "9093/tcp").rsplit(":", 1)[1]
        base = "http://127.0.0.1:" + port

        def request(path, payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            with urlopen(
                Request(
                    base + path, data=data, headers={"Content-Type": "application/json"}
                ),
                timeout=5,
            ) as r:
                content = r.read()
                return json.loads(content) if content else None

        for _ in range(50):
            try:
                request("/api/v2/status")
                break
            except (URLError, ConnectionError):
                time.sleep(0.1)
        else:
            raise AssertionError(run("docker", "logs", cid))
        now = datetime.datetime.now(datetime.timezone.utc)
        alerts = []
        expectations = {}
        for index, rule in enumerate(config["inhibit_rules"]):
            source_name = rule["source_matchers"][0].split("=", 1)[1].strip('"')
            target_name = rule["target_matchers"][0].split("=", 1)[1].strip('"')
            entity = {
                "cluster": "natsume",
                "namespace": f"app-{index}",
                "instance": f"host-{index}",
                "node": f"node-{index}",
                "persistentvolumeclaim": f"pvc-{index}",
                "mountpoint": "/",
                "device": "/dev/vda1",
                "name": f"cert-{index}",
                "disk": f"disk-{index}",
                "job": "http-get",
            }

            def alert(labels):
                alerts.append(
                    {
                        "labels": labels,
                        "annotations": {"summary": "local inhibition test"},
                        "startsAt": (now - datetime.timedelta(minutes=1)).isoformat(),
                        "endsAt": (now + datetime.timedelta(hours=1)).isoformat(),
                    }
                )

            alert(
                {
                    **entity,
                    "alertname": source_name,
                    "severity": "critical",
                    "case": f"{index}-source",
                }
            )

            def target(suffix, labels, expected):
                case = f"{index}-{suffix}"
                alert(
                    {
                        **labels,
                        "alertname": target_name,
                        "severity": "warning",
                        "case": case,
                    }
                )
                expectations[case] = expected

            target("same", entity, True)
            for key in rule["equal"]:
                target("different-" + key, {**entity, key: "different"}, False)
                target(
                    "missing-" + key,
                    {k: v for k, v in entity.items() if k != key},
                    False,
                )
            # 同じ対象でも別 family は抑制しない。
            alert(
                {
                    **entity,
                    "alertname": "UnrelatedWarning",
                    "severity": "warning",
                    "case": f"{index}-unrelated",
                }
            )
            expectations[f"{index}-unrelated"] = False
        # HostDown / MetricsStale だけで Pod warning を隠さない。
        for name in ("NodeExporterAbsent", "MetricsStale", "KubePodNotReady"):
            case = "no-broad-inhibition-" + name
            alert(
                {
                    "cluster": "natsume",
                    "namespace": "app",
                    "instance": "node-a",
                    "pod": "pod-a",
                    "alertname": name,
                    "severity": "warning" if name == "KubePodNotReady" else "critical",
                    "case": case,
                }
            )
            expectations[case] = False
        request("/api/v2/alerts", alerts)
        for _ in range(50):
            got = {
                a["labels"]["case"]: bool(a["status"]["inhibitedBy"])
                for a in request("/api/v2/alerts")
            }
            mismatch = {
                case: (want, got.get(case))
                for case, want in expectations.items()
                if got.get(case) != want
            }
            if not mismatch:
                break
            time.sleep(0.2)
        assert not mismatch, mismatch
        print(
            f"local Alertmanager inhibition: {len(expectations)} same/different/missing entity cases OK"
        )
    finally:
        run("docker", "rm", "-f", cid)

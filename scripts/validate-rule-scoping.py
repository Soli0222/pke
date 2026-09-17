#!/usr/bin/env python3
"""ローカルの偽 API と実 Alloy / promtool でルールのクラスタ分離を検証する。"""
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
ALLOY = 'grafana/alloy:v1.19.2'
PROMETHEUS = 'prom/prometheus:v3.5.0'
UID = '00000000-0000-0000-0000-000000000736'


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def component(source):
    start = source.index('mimir.rules.kubernetes "default" {')
    end = source.index('\n}\n', start)
    return source[start:end + 3]


def main():
    # 本番の認証情報や kubeconfig は使わない。両クラスタに同じ CR を返す。
    rules = [
        {'record': 'pke_test:sum', 'expr': 'sum(pke_test_metric)', 'labels': {'cluster': 'wrong'}},
        {'record': 'pke_test:absent', 'expr': 'absent(pke_test_only_meruto)'},
        {'record': 'pke_test:range', 'expr': 'sum(avg_over_time(pke_test_metric[5m]))'},
        {'record': 'pke_test:replace', 'expr': 'sum(pke_test_metric{cluster="wrong"})'},
        {'record': 'pke_test:db', 'expr': 'sum by (cnpg_cluster) (cnpg_test_value) / on (cnpg_cluster) sum by (cnpg_cluster) (cnpg_test_divisor)'},
        {'record': 'pke_test:db_selected', 'expr': 'sum by (cnpg_cluster) (cnpg_test_value{cnpg_cluster="db-a"})'},
        {'alert': 'ScopeTest', 'expr': 'sum(pke_test_metric) > 0', 'for': '1m', 'labels': {'severity': 'warning'}},
        {'alert': 'AbsentTest', 'expr': 'absent(pke_test_only_meruto)', 'for': '1m'},
    ]
    resource = {'apiVersion': 'monitoring.coreos.com/v1', 'kind': 'PrometheusRule',
                'metadata': {'namespace': 'test', 'name': 'scoping', 'uid': UID, 'resourceVersion': '1'},
                'spec': {'groups': [{'name': 'scoping', 'interval': '1m', 'rules': rules}]}}
    namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'test', 'resourceVersion': '1'}}
    stored = {}
    lock = threading.Lock()
    stopping = threading.Event()

    class API(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200, content_type='application/json'):
            payload = (json.dumps(value) if content_type == 'application/json' else yaml.safe_dump(value)).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlsplit(self.path).path
            if 'watch=true' in self.path:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                if 'sendInitialEvents=true' in self.path:
                    obj = namespace if path == '/api/v1/namespaces' else resource
                    bookmark = {'apiVersion': obj['apiVersion'], 'kind': obj['kind'],
                                'metadata': {'resourceVersion': '1', 'annotations': {'k8s.io/initial-events-end': 'true'}}}
                    for event in [{'type': 'ADDED', 'object': obj}, {'type': 'BOOKMARK', 'object': bookmark}]:
                        self.wfile.write(json.dumps(event).encode() + b'\n')
                    self.wfile.flush()
                stopping.wait(10)
            elif path == '/api/v1/namespaces':
                self.reply({'apiVersion': 'v1', 'kind': 'NamespaceList', 'metadata': {'resourceVersion': '1'}, 'items': [namespace]})
            elif path == '/apis/monitoring.coreos.com/v1/prometheusrules':
                self.reply({'apiVersion': 'monitoring.coreos.com/v1', 'kind': 'PrometheusRuleList', 'metadata': {'resourceVersion': '1'}, 'items': [resource]})
            elif path == '/prometheus/config/v1/rules':
                with lock:
                    self.reply(stored, content_type='application/yaml')
            else:
                self.reply({'error': path}, 404)

        def do_POST(self):
            assert self.headers.get('X-Scope-OrgID') == 'anonymous'
            prefix = '/prometheus/config/v1/rules/'
            assert self.path.startswith(prefix), self.path
            name = unquote(self.path[len(prefix):])
            group = yaml.safe_load(self.rfile.read(int(self.headers['Content-Length'])))
            with lock:
                stored[name] = [group]
            self.reply({}, 202)

    server = ThreadingHTTPServer(('0.0.0.0', 0), API)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://host.docker.internal:{server.server_port}'
    containers = []
    try:
        with tempfile.TemporaryDirectory(prefix='pke-rule-scope-') as tmp:
            out = Path(tmp)
            out.chmod(0o755)
            (out / 'kubeconfig').write_text(yaml.safe_dump({
                'apiVersion': 'v1', 'kind': 'Config', 'current-context': 'test',
                'clusters': [{'name': 'test', 'cluster': {'server': endpoint}}],
                'contexts': [{'name': 'test', 'context': {'cluster': 'test', 'user': 'test'}}],
                'users': [{'name': 'test', 'user': {}}],
            }))
            for cluster in ('natsume', 'meruto'):
                source = yaml.safe_load((ROOT / f'flux/clusters/{cluster}/apps/alloy/alloy-config.yaml').read_text())['data']['config.alloy']
                # 接続先と TLS だけを偽 API 用に差し替え、変換設定はそのまま使用する。
                config = re.sub(r'address = "[^"]+"', f'address = "{endpoint}"', component(source))
                config = re.sub(r'\n  tls_config \{.*?\n  \}', '', config, flags=re.S)
                (out / f'{cluster}.alloy').write_text(config)
                cid = run('docker', 'run', '-d', '--add-host=host.docker.internal:host-gateway',
                          '-e', 'KUBECONFIG=/work/kubeconfig', '-v', f'{out}:/work:ro',
                          ALLOY, 'run', f'/work/{cluster}.alloy')
                containers.append(cid)
                ns = f'{"alloy" if cluster == "natsume" else "meruto"}/test/scoping/{UID}'
                for _ in range(60):
                    with lock:
                        ready = ns in stored
                    if ready:
                        break
                    time.sleep(0.5)
                else:
                    raise AssertionError(run('docker', 'logs', cid))
                run('docker', 'rm', '-f', cid)
                containers.remove(cid)
                for rule in stored[ns][0]['rules']:
                    assert rule['labels']['cluster'] == cluster, rule
                    assert f'cluster="{cluster}"' in rule['expr'], rule
                    assert 'cluster="wrong"' not in rule['expr'], rule
                print(f'{cluster}: real Alloy rewrites queries and alert/recording labels; namespace={ns}', flush=True)

            assert len(stored) == 2, stored
            groups = []
            for ns, group in stored.items():
                groups.append(dict(group[0], name=ns))
            (out / 'rules.yaml').write_text(yaml.safe_dump({'groups': groups}))
            inputs = []
            for cluster, value in [('natsume', 2), ('meruto', 7)]:
                inputs.append({'series': f'pke_test_metric{{cluster="{cluster}"}}', 'values': f'{value}+0x10'})
                for db, multiple in [('db-a', 1), ('db-b', 3)]:
                    for name, number in [('cnpg_test_value', value * multiple), ('cnpg_test_divisor', 2)]:
                        inputs.append({'series': f'{name}{{cluster="{cluster}",cnpg_cluster="{db}"}}', 'values': f'{number}+0x10'})
            inputs.append({'series': 'pke_test_only_meruto{cluster="meruto"}', 'values': '1+0x10'})
            expressions = []
            for name in ('sum', 'range', 'replace'):
                expressions.append({'expr': f'pke_test:{name}', 'eval_time': '10m', 'exp_samples': [
                    {'labels': f'pke_test:{name}{{cluster="{c}"}}', 'value': v} for c, v in [('natsume', 2), ('meruto', 7)]]})
            expressions.append({'expr': 'pke_test:absent', 'eval_time': '10m', 'exp_samples': [
                {'labels': 'pke_test:absent{cluster="natsume"}', 'value': 1}]})
            for name, divisor, dbs in [('db', 2, [('db-a', 1), ('db-b', 3)]), ('db_selected', 1, [('db-a', 1)])]:
                expressions.append({'expr': f'pke_test:{name}', 'eval_time': '10m', 'exp_samples': [
                    {'labels': f'pke_test:{name}{{cluster="{c}",cnpg_cluster="{db}"}}', 'value': v * m / divisor}
                    for c, v in [('natsume', 2), ('meruto', 7)] for db, m in dbs]})
            tests = {'rule_files': ['rules.yaml'], 'evaluation_interval': '1m', 'tests': [{
                'interval': '1m', 'input_series': inputs, 'promql_expr_test': expressions,
                'alert_rule_test': [
                    {'eval_time': '10m', 'alertname': 'ScopeTest', 'exp_alerts': [
                        {'exp_labels': {'cluster': c, 'severity': 'warning'}} for c in ('natsume', 'meruto')]},
                    {'eval_time': '10m', 'alertname': 'AbsentTest', 'exp_alerts': [{'exp_labels': {'cluster': 'natsume'}}]},
                ],
            }]}
            (out / 'tests.yaml').write_text(yaml.safe_dump(tests))
            print(run('docker', 'run', '--rm', '--network', 'none', '-v', f'{out}:/work:ro', '-w', '/work',
                      '--entrypoint', '/bin/promtool', PROMETHEUS, 'test', 'rules', 'tests.yaml'), flush=True)
    finally:
        for cid in containers:
            run('docker', 'rm', '-f', cid)
        stopping.set()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()

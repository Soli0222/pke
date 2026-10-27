#!/usr/bin/env python3
"""合成監査ログだけを使い、実 Alloy で除去・主体・ラベル・再開を検証する。"""
import json
import subprocess
import tempfile
import time
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
ALLOY = 'grafana/alloy:v1.19.2'
CANARY = 'AUDIT_SECRET_CANARY_DO_NOT_FORWARD'


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def events(container):
    logs = run('docker', 'logs', container)
    assert CANARY not in logs, 'Secret canary reached Alloy output'
    rows = []
    for line in logs.splitlines():
        if not line.startswith('{'):
            continue
        row = json.loads(line)
        if row.get('msg') == 'received log entry':
            rows.append((json.loads(row['entry']), row['labels']))
    return rows


def wait_for(container, expected):
    for _ in range(40):
        rows = events(container)
        if {r[0]['audit_id'] for r in rows} == expected:
            return rows
        time.sleep(0.5)
    raise AssertionError(f'Expected {expected}; got {rows}')


def fixture(audit_id, code=200):
    return json.dumps({
        'apiVersion': 'audit.k8s.io/v1', 'kind': 'Event', 'auditID': audit_id,
        'stage': 'ResponseComplete', 'verb': 'get',
        'user': {'username': 'oidc:pke:alice', 'groups': ['ops', 'system:authenticated'],
                 'extra': {'untrusted': [CANARY]}},
        'impersonatedUser': {'username': 'test-subject', 'groups': ['reader']},
        'objectRef': {'resource': 'secrets', 'namespace': 'test', 'name': 'fixture', 'apiVersion': 'v1'},
        'responseStatus': {'code': code, 'message': CANARY},
        'requestReceivedTimestamp': '2026-01-01T00:00:00Z',
        'stageTimestamp': '2026-01-01T00:00:01Z',
        'requestURI': '/api/v1/secrets?token=' + CANARY, 'userAgent': CANARY,
        'requestObject': {'password': CANARY}, 'responseObject': {'data': CANARY},
        'annotations': {'untrusted': CANARY}, 'sourceIPs': [CANARY],
    }) + '\n'


def main():
    policy = yaml.safe_load((ROOT / 'ansible/roles/install-k3s/files/audit-policy.yaml').read_text())
    assert policy['rules'] == [{'level': 'Metadata'}]
    assert policy['omitStages'] == ['RequestReceived']
    defaults = yaml.safe_load((ROOT / 'ansible/roles/install-alloy/defaults/main.yaml').read_text())
    server = yaml.safe_load((ROOT / 'ansible/inventories/group_vars/k3s_server.yaml').read_text())
    env = Environment(undefined=StrictUndefined)
    template = env.from_string((ROOT / 'ansible/roles/install-alloy/templates/loki.alloy.j2').read_text())
    with tempfile.TemporaryDirectory(prefix='pke-audit-') as temp:
        out = Path(temp)
        out.chmod(0o755)
        for host, cluster, is_server in [('natsume-03', 'natsume', True),
                                         ('natsume-08', 'natsume', False),
                                         ('meruto-01', 'meruto', True)]:
            values = defaults | {'cluster': cluster, 'ansible_hostname': host,
                                 'tls_cert_path': '/unused/crt', 'tls_key_path': '/unused/key'}
            if is_server:
                values |= server
            source = template.render(**values)
            (out / 'full.alloy').write_text(source)
            run('docker', 'run', '--rm', '--network', 'none', '-v', f'{out}:/work:ro',
                ALLOY, 'validate', '/work/full.alloy')
            assert ('loki.source.file "kubernetes_audit"' in source) == is_server
            if not is_server:
                print(f'{host}: full config valid; no API audit collector', flush=True)
                continue
            # 本番と同じ file source / process / common labels。出口のみ stdout に交換。
            source = source[source.index('local.file_match "kubernetes_audit"'):source.index('loki.write "endpoint"')]
            source = source.replace('/var/log/kubernetes-audit/audit.log', '/tmp/pke-audit.log')
            source = source.replace('loki.write.endpoint.receiver', 'loki.echo.test.receiver')
            source += '\nlogging { format = "json" }\nloki.echo "test" {}\n'
            (out / 'test.alloy').write_text(source)
            log = out / 'audit.log'
            log.write_text(fixture('allowed') + fixture('denied', 403)
                           + '{malformed ' + CANARY + '\n' + json.dumps({'message': CANARY}) + '\n')
            container = run('docker', 'run', '-d', '--network', 'none', '-v', f'{out}:/work:ro',
                            ALLOY, 'run', '--disable-reporting', '/work/test.alloy')
            try:
                # tail 対象は Linux filesystem 内に置く（macOS bind mount の inode cache を避ける）。
                run('docker', 'cp', str(log), container + ':/tmp/pke-audit.log')
                rows = wait_for(container, {'allowed', 'denied'})
                for entry, labels in rows:
                    assert entry['username'] == 'oidc:pke:alice'
                    assert json.loads(entry['groups']) == ['ops', 'system:authenticated']
                    assert entry['impersonated_username'] == 'test-subject'
                    assert entry['status_code'] == ('403' if entry['audit_id'] == 'denied' else '200')
                    assert entry['resource'] == 'secrets' and entry['namespace'] == 'test'
                    assert entry['request_time'] == '2026-01-01T00:00:00Z'
                    assert entry['_entry'] == 'kubernetes audit'
                    assert labels == ('{cluster="' + cluster + '", hostname="' + host
                                      + '", job="kubernetes-audit", origin="audit"}')
                # rename/create rotation と同一 storage path での再起動を通した追尾。
                run('docker', 'exec', container, 'mv', '/tmp/pke-audit.log', '/tmp/pke-audit.log.1')
                log.write_text(fixture('rotated'))
                run('docker', 'cp', str(log), container + ':/tmp/pke-audit.log')
                wait_for(container, {'allowed', 'denied', 'rotated'})
                run('docker', 'stop', container)
                with log.open('a') as stream:
                    stream.write(fixture('after-restart'))
                run('docker', 'cp', str(log), container + ':/tmp/pke-audit.log')
                run('docker', 'start', container)
                rows = wait_for(container, {'allowed', 'denied', 'rotated', 'after-restart'})
                assert len(rows) == 4, 'Unexpected replay after graceful restart'
                print(f'{host}: secrets removed, identity/status preserved, bounded labels, rotation/restart OK', flush=True)
            finally:
                run('docker', 'rm', '-f', container)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""実 Secret を使わず Alloy の設定と cluster ラベル変換を検証する。"""
import json
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
ALLOY = 'grafana/alloy:v1.19.2'


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def http(url, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    with urlopen(Request(url, data=payload, headers={'Content-Type': 'application/json'}), timeout=5) as r:
        return json.load(r)


def main():
    with tempfile.TemporaryDirectory(prefix='pke-labels-') as temp:
        out = Path(temp)
        out.chmod(0o755)
        configs = {}
        for cluster in ('natsume', 'meruto'):
            run('kubectl', 'kustomize', f'flux/clusters/{cluster}')
            configs[cluster] = yaml.safe_load((ROOT / f'flux/clusters/{cluster}/apps/alloy/alloy-config.yaml').read_text())['data']['config.alloy']
            (out / f'{cluster}.alloy').write_text(configs[cluster])
        defaults = yaml.safe_load((ROOT / 'ansible/roles/install-alloy/defaults/main.yaml').read_text())
        common = yaml.safe_load((ROOT / 'ansible/inventories/group_vars/all.yaml').read_text())
        env = Environment(undefined=StrictUndefined)
        for host in ('natsume-03', 'natsume-08', 'meruto-01'):
            values = defaults | common | yaml.safe_load((ROOT / f'ansible/inventories/host_vars/{host}.yaml').read_text())
            values['ansible_hostname'] = host
            for enabled in (True, False):
                values['falco'] = dict(values['falco'], enabled=enabled)
                rendered = env.from_string((ROOT / 'ansible/roles/install-alloy/templates/prometheus.alloy.j2').read_text()).render(**values)
                if host != 'natsume-08':
                    rendered += '\n' + env.from_string((ROOT / 'ansible/roles/install-alloy/templates/etcd.alloy.j2').read_text()).render(**values)
                (out / f'{host}-{enabled}.alloy').write_text(rendered)
        for config in out.glob('*.alloy'):
            run('docker', 'run', '--rm', '--network', 'none', '-v', f'{out}:/work:ro', ALLOY,
                'validate', '--stability.level=experimental', '/work/' + config.name)
        print('Both Kubernetes configs / all three host templates (Falco on/off, etcd): OK', flush=True)

        # 独立したローカル受信先へ実際に remote_write し、保存後のラベルを確認する。
        (out / 'prometheus.yaml').write_text('global:\n  scrape_interval: 1s\nscrape_configs: []\n')
        containers = []
        try:
            receiver = run('docker', 'run', '-d', '-p', '127.0.0.1::9090', '-v', f'{out}:/work:ro',
                           'prom/prometheus:v3.5.0', '--config.file=/work/prometheus.yaml', '--web.enable-remote-write-receiver', '--web.user-assets=/work')
            containers.append(receiver)
            query_url = 'http://' + run('docker', 'port', receiver, '9090/tcp') + '/api/v1/query?'
            for cluster, source in configs.items():
                # 本番の共通 relabel ブロックをそのまま使う。
                rules = source.split('prometheus.relabel "cluster_labels" {', 1)[1].split('prometheus.remote_write "default"', 1)[0]
                config = 'prometheus.relabel "cluster_labels" {' + rules + '''
prometheus.remote_write "default" {
  endpoint {
    url = "http://localhost:9090/api/v1/write"
    queue_config { batch_send_deadline = "1s" }
  }
}
prometheus.scrape "test" {
  targets = [{__address__ = "localhost:9090"}]
  metrics_path = "/user/fixtures.txt"
  scrape_interval = "1s"
  scrape_timeout = "1s"
  forward_to = [prometheus.relabel.cluster_labels.receiver]
}
'''
                (out / 'test.alloy').write_text(config)
                sender = run('docker', 'run', '-d', '--network', 'container:' + receiver, '-v', f'{out}:/work:ro',
                             ALLOY, 'run', '/work/test.alloy')
                containers.append(sender)
                cases = [('db', 'cnpg_collector_up', {'cluster': 'misskey-cluster'}),
                         ('db_without_cluster', 'cnpg_pg_replication_streaming_replicas', {'cnpg_cluster': 'misskey-cluster'}),
                         ('missing', 'node_uname_info', {}),
                         ('empty', 'node_uname_info', {'cluster': ''}),
                         ('wrong', 'node_uname_info', {'cluster': 'wrong'})]
                fixtures = []
                for case, name, labels in cases:
                    attrs = ','.join(f'{k}={json.dumps(v)}' for k, v in dict(labels, test_case=case).items())
                    fixtures.append(f'{name}{{{attrs}}} 1')
                (out / 'fixtures.txt').write_text('\n'.join(fixtures) + '\n')
                expected = {c[0] for c in cases}
                for attempt in range(30):
                    result = http(query_url + urlencode({'query': '{test_case!="",cluster="' + cluster + '"}'}))['data']['result']
                    if {r['metric']['test_case'] for r in result} == expected:
                        break
                    time.sleep(1)
                else:
                    print(run('docker', 'logs', sender))
                    print(http(query_url + urlencode({'query': '{__name__!=""}'})))
                    raise AssertionError(result)
                for row in result:
                    labels = row['metric']
                    assert 'pke_cluster' not in labels
                    if labels['test_case'].startswith('db'):
                        assert labels['cnpg_cluster'] == 'misskey-cluster', labels
                    else:
                        assert 'cnpg_cluster' not in labels, labels
                run('docker', 'rm', '-f', sender)
                containers.remove(sender)
                print(f'{cluster}: CNPG DB name preserved; missing/empty/wrong cluster normalized: OK', flush=True)
        finally:
            for cid in reversed(containers):
                run('docker', 'rm', '-f', cid)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""実dashboard queryでcluster分離・Ready件数・counter増分の意味を検証する。"""
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def query(file, panel):
    document = json.loads((ROOT / f'grafana/dashboards/platform/{file}.json').read_text())
    expr = next(p for p in document['spec']['panels'] if p['id'] == panel)['targets'][0]['expr']
    for key, value in {'$cluster': 'natsume', '${instance:regex}': '.*', '${namespace:regex}': '.*', '$__range': '5m', '$namespace': 'app', '$cnpg_cluster': 'db', '${pod:regex}': '.*'}.items():
        expr = expr.replace(key, value)
    return expr


def check(file, panel, value):
    return {'expr': query(file, panel), 'eval_time': '5m', 'exp_samples': [] if value is None else [{'labels': '{}', 'value': value}]}


def main():
    series = [
        ('etcd_server_id{cluster="natsume",instance="node-a",server_id="a",job="etcd"}', '1+0x5'),
        ('etcd_server_id{cluster="meruto",instance="node-b",server_id="b",job="etcd"}', '1+0x5'),
        ('etcd_server_has_leader{cluster="natsume",instance="node-a",job="etcd"}', '1+0x5'),
        ('etcd_server_has_leader{cluster="meruto",instance="node-b",job="etcd"}', '0+0x5'),
        ('etcd_server_proposals_failed_total{cluster="natsume",instance="node-a",job="etcd"}', '0+1x5'),
        ('etcd_server_proposals_failed_total{cluster="meruto",instance="node-b",job="etcd"}', '0+10x5'),
        ('etcd_server_leader_changes_seen_total{cluster="natsume",instance="node-a",job="etcd"}', '100+0x5'),
        ('certmanager_certificate_ready_status{cluster="natsume",job="cert-manager",exported_namespace="app",name="ready",condition="True"}', '1+0x5'),
        ('certmanager_certificate_ready_status{cluster="natsume",job="cert-manager",exported_namespace="app",name="not-ready",condition="True"}', '0+0x5'),
        ('certmanager_certificate_ready_status{cluster="meruto",job="cert-manager",exported_namespace="app",name="other",condition="True"}', '1+0x5'),
        ('certmanager_certificate_expiration_timestamp_seconds{cluster="natsume",job="cert-manager",exported_namespace="app",name="ready"}', '99999999+0x5'),
    ]
    tests = [
        {'name': 'cluster isolation and value-aware counts', 'interval': '1m', 'input_series': [{'series': s, 'values': v} for s, v in series], 'promql_expr_test': [check('etcd', 93, 1), check('etcd', 90, 1), check('etcd', 92, 5), check('etcd', 91, 0), check('cert-manager', 1, 1), check('cert-manager', 3, 0)]},
        {'name': 'absence is not normal zero', 'interval': '1m', 'input_series': [], 'promql_expr_test': [check('etcd', 93, None), check('etcd', 90, None), check('etcd', 92, None), check('cert-manager', 1, None), check('cert-manager', 3, None)]},
        {'name': 'counter reset is not proposal increase', 'interval': '1m', 'input_series': [{'series': series[4][0], 'values': '10 10 0 0 0 0'}], 'promql_expr_test': [check('etcd', 92, 0)]},
    ]
    db_labels = 'cluster="natsume",namespace="app",cnpg_cluster="db",pod="db-1"'
    collector = {'series': 'cnpg_collector_up{' + db_labels + '}', 'values': '1x5'}
    backup_metrics = [
        ('barman_cloud_cloudnative_pg_io_last_available_backup_timestamp', '120x5'),
        ('barman_cloud_cloudnative_pg_io_first_recoverability_point', '60x5'),
        ('cnpg_pg_stat_archiver_seconds_since_last_archival', '40x5'),
    ]
    tests += [
        {'name': 'Barman success, recovery point and WAL stay in selected DB',
         'interval': '1m',
         'input_series': [collector] + [
             {'series': metric + '{' + labels + '}', 'values': values}
             for metric, value in backup_metrics
             for labels, values in [(db_labels, value),
                                    (db_labels.replace('natsume', 'meruto'), '999x5'),
                                    (db_labels.replace('cnpg_cluster="db"', 'cnpg_cluster="other"'), '888x5')]
         ],
         'promql_expr_test': [check('cloudnative-pg', 360, 120000),
                             check('cloudnative-pg', 1005, 60000),
                             check('cloudnative-pg', 362, 40)]},
        {'name': 'Barman zero success is unverified', 'interval': '1m',
         'input_series': [collector] + [
             {'series': metric + '{' + db_labels + '}', 'values': '0x5'}
             for metric, _ in backup_metrics[:2]
         ],
         'promql_expr_test': [check('cloudnative-pg', 360, -1), check('cloudnative-pg', 1005, -1)]},
        {'name': 'DB without plugin metrics is unverified', 'interval': '1m',
         'input_series': [collector],
         'promql_expr_test': [check('cloudnative-pg', panel, -1) for panel in [360, 1005, 362]]},
        {'name': 'DB absence is not successful backup', 'interval': '1m',
         'input_series': [],
         'promql_expr_test': [check('cloudnative-pg', panel, None) for panel in [360, 1005, 362]]},
    ]
    payload = yaml.safe_dump({'rule_files': [], 'evaluation_interval': '1m', 'tests': tests})
    subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', '/bin/sh', 'prom/prometheus:v3.5.0', '-c', 'cat > /tmp/test.yaml && promtool test rules /tmp/test.yaml'], input=payload, text=True, check=True)


if __name__ == '__main__':
    main()

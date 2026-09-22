#!/usr/bin/env python3
"""CoreDNSの現行metric・cluster/job分離・cache比率を実queryで検証する。"""
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    document = json.loads((ROOT / 'grafana/dashboards/platform/coredns.json').read_text())

    def walk(panels):
        for panel in panels:
            yield panel
            yield from walk(panel.get('panels', []))

    panels = {p['id']: p for p in walk(document['spec']['panels'])}
    for panel in panels.values():
        for target in panel.get('targets', []):
            expr = target.get('expr', '')
            for obsolete in ['coredns_forward_requests_total', 'coredns_forward_responses_total', 'coredns_forward_request_duration_seconds', 'coredns_forward_conn_cache_hits_total', 'coredns_dnssec_', 'coredns_dns_do_requests_total']:
                assert obsolete not in expr, obsolete

    def query(panel, ref='A'):
        expr = next(t['expr'] for t in panels[panel]['targets'] if t.get('refId') == ref)
        return expr.replace('$cluster', 'natsume').replace('$instance', 'node-a')

    def check(panel, value, ref='A'):
        return {'expr': 'sum(' + query(panel, ref) + ')', 'eval_time': '5m', 'exp_samples': [] if value is None else [{'labels': '{}', 'value': value}]}

    def series(metric, labels, values, cluster='natsume', job='coredns'):
        labels = dict(cluster=cluster, job=job, instance='node-a', **labels)
        return {'series': metric + '{' + ','.join(f'{k}={json.dumps(v)}' for k, v in labels.items()) + '}', 'values': values}

    inputs = [
        series('coredns_proxy_request_duration_seconds_count', {'proxy_name': 'forward', 'to': '9.9.9.9:53', 'rcode': 'NOERROR'}, '0+60x5'),
        series('coredns_proxy_request_duration_seconds_count', {'proxy_name': 'other', 'to': '9.9.9.9:53', 'rcode': 'NOERROR'}, '0+600x5'),
        series('coredns_proxy_request_duration_seconds_count', {'proxy_name': 'forward', 'to': '9.9.9.9:53', 'rcode': 'NOERROR'}, '0+600x5', cluster='meruto'),
        series('coredns_proxy_conn_cache_hits_total', {'proxy_name': 'forward', 'proto': 'udp', 'to': '9.9.9.9:53'}, '0+8x5'),
        series('coredns_proxy_conn_cache_misses_total', {'proxy_name': 'forward', 'proto': 'udp', 'to': '9.9.9.9:53'}, '0+2x5'),
        series('coredns_cache_requests_total', {}, '0+600x5'),
        series('coredns_cache_hits_total', {'type': 'success'}, '0+360x5'),
        series('coredns_cache_hits_total', {'type': 'denial'}, '0+180x5'),
        series('process_cpu_seconds_total', {}, '0+30x5'),
        series('process_cpu_seconds_total', {}, '0+600x5', job='other-exporter'),
    ]
    tests = [
        {'name': 'current forward counters isolate cluster and proxy; connection and DNS caches differ', 'interval': '1m', 'input_series': inputs, 'promql_expr_test': [check(72, 1), check(105, 1), check(53, 1), check(38, 0.8), check(38, 0.2, 'B'), check(24, 0.6), check(24, 0.3, 'B'), check(24, 0.1, 'C'), check(119, 0.5)]},
        {'name': 'missing metrics stay absent', 'interval': '1m', 'input_series': [], 'promql_expr_test': [check(72, None), check(38, None), check(24, None), check(119, None)]},
    ]
    payload = yaml.safe_dump({'rule_files': [], 'evaluation_interval': '1m', 'tests': tests})
    subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', '/bin/sh', 'prom/prometheus:v3.5.0', '-c', 'cat > /tmp/test.yaml && promtool test rules /tmp/test.yaml'], input=payload, text=True, check=True)


if __name__ == '__main__':
    main()

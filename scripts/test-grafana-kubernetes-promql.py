#!/usr/bin/env python3
"""実dashboard queryでnamespace境界、taint件数、欠測と0を検証する。"""
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels', []))


def query(panel_id, ref):
    spec = json.loads((ROOT / 'grafana/dashboards/platform/kubernetes.json').read_text())['spec']
    panel = next(p for p in panels(spec['panels']) if p['id'] == panel_id)
    expr = next(t['expr'] for t in panel['targets'] if t['refId'] == ref)
    for key, value in {'$cluster': 'natsume', '${node:regex}': '.*', '${namespace:regex}': '.*', '${pod:regex}': '.*', '${container:regex}': '.*', '$__rate_interval': '5m'}.items():
        expr = expr.replace(key, value)
    return expr


def series(name, labels, values):
    return {'series': name + '{' + ','.join(f'{key}="{value}"' for key, value in labels.items()) + '}', 'values': values}


def sample(labels, value):
    return {'labels': '{' + ','.join(f'{key}="{value}"' for key, value in labels.items()) + '}', 'value': value}


def check(pid, ref, samples):
    return {'expr': query(pid, ref), 'eval_time': '5m', 'exp_samples': samples}


def main():
    ksm = {'cluster': 'natsume', 'job': 'kube-state-metrics'}
    nodes = [series('kube_node_info', {**ksm, 'node': node}, '1+0x5') for node in ['node-a', 'node-b']]
    nodes.append(series('kube_node_info', {**ksm, 'cluster': 'meruto', 'node': 'node-a'}, '1+0x5'))
    taints = [series('kube_node_spec_taint', {**ksm, 'node': 'node-a', 'key': key}, '1+0x5') for key in ['dedicated', 'maintenance']]
    tests = [
        {'name': 'no taints with known nodes', 'input_series': nodes, 'promql_expr_test': [check(88, 'B', [sample({'cluster': 'natsume'}, 2)]), check(88, 'A', [sample({'cluster': 'natsume'}, 0)])]},
        {'name': 'two taints on one node counted once', 'input_series': nodes + taints, 'promql_expr_test': [check(88, 'B', [sample({'cluster': 'natsume'}, 1)]), check(88, 'A', [sample({'cluster': 'natsume'}, 1)])]},
        {'name': 'missing inventory is not normal zero', 'input_series': [], 'promql_expr_test': [check(88, 'B', []), check(88, 'A', [])]},
    ]
    data = []
    for ns, value in [('one', 2), ('two', 7)]:
        base = {**ksm, 'namespace': ns, 'pod': 'same-pod'}
        data += [series('kube_pod_info', {**base, 'node': 'node-a', 'host_network': 'false'}, '1+0x5'), series('kube_pod_created', base, '100+0x5')]
        for container in ['app', 'sidecar']:
            data += [series('kube_pod_container_info', {**base, 'container': container}, '1+0x5'), series('kube_pod_container_status_restarts_total', {**base, 'container': container}, f'{value}+0x5')]
        cad = {**base, 'job': 'prometheus.scrape.kubernetes_cadvisor', 'node': 'node-a'}
        data += [series('container_network_receive_bytes_total', {**cad, 'interface': 'eth0'}, f'0+{60 * value}x5'), series('container_cpu_usage_seconds_total', {**cad, 'container': 'app'}, '0+0x5'), series('container_memory_working_set_bytes', {**cad, 'container': 'app'}, f'{value}+0x5')]
    restart_samples = []
    for ns, value in [('one', 2), ('two', 7)]:
        for container in ['app', 'sidecar']:
            restart_samples.append(sample({'cluster': 'natsume', 'namespace': ns, 'pod': 'same-pod', 'container': container, 'node': 'node-a'}, value))
    tests.append({'name': 'same pod in two namespaces and multiple containers', 'input_series': data, 'promql_expr_test': [
        check(47, 'H', restart_samples),
        check(47, 'R', [{**s, 'value': 200} for s in restart_samples]),
        check(77, 'A', [sample({'cluster': 'natsume', 'namespace': ns, 'pod': 'same-pod', 'node': 'node-a'}, value * 8) for ns, value in [('one', 2), ('two', 7)]]),
        check(90, 'A', [sample({'cluster': 'natsume', 'namespace': ns, 'container': 'app'}, value) for ns, value in [('one', 2), ('two', 7)]]),
        check(86, 'A', [sample({'cluster': 'natsume', 'namespace': ns}, 0) for ns in ['one', 'two']]),
        check(27, 'A', []),  # no configured memory limit is not 0% utilization
    ]})
    pvc = {'cluster': 'natsume', 'namespace': 'one', 'persistentvolumeclaim': 'unmounted'}
    tests.append({'name': 'known PVC with missing capacity', 'input_series': [series('kube_persistentvolumeclaim_info', {**pvc, 'job': 'kube-state-metrics'}, '1+0x5')], 'promql_expr_test': [check(92, 'E', [sample(pvc, 1)]), check(92, 'B', []), check(92, 'F', [sample(pvc, 1)])]})
    payload = yaml.safe_dump({'rule_files': [], 'evaluation_interval': '1m', 'tests': [{'interval': '1m', **test} for test in tests]})
    subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', '/bin/sh', 'prom/prometheus:v3.5.0', '-c', 'cat > /tmp/test.yaml && promtool test rules /tmp/test.yaml'], input=payload, text=True, check=True)


if __name__ == '__main__':
    main()

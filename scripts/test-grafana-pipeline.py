#!/usr/bin/env python3
"""実dashboard queryで欠測・停止・クラスタ分離・旧通知counterを検証する。"""
import copy
import importlib.util
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pipeline_inventory', ROOT / 'scripts/sync-grafana-pipeline-inventory.py')
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def main():
    document = json.loads((ROOT / inventory.DASHBOARD).read_text())
    data = inventory.expected()
    assert inventory.synchronize(copy.deepcopy(document), data) == document
    hosts, targets, paths, groups = data
    panels = {p['id']: p for p in document['spec']['panels']}

    def query(panel, cluster='natsume', ref=0):
        return panels[panel]['targets'][ref]['expr'].replace('$cluster', cluster).replace('$__rate_interval', '5m').replace('$__range', '5m')

    def check(panel, value, cluster='natsume', time='5m', wrapper=None):
        expr = query(panel, cluster)
        if wrapper:
            expr = wrapper.format(expr=expr)
        return {'expr': expr, 'eval_time': time, 'exp_samples': [] if value is None else [{'labels': '{}', 'value': value}]}

    def metric(name, labels, values):
        return {'series': name + '{' + ','.join(f'{k}={json.dumps(v)}' for k, v in labels.items()) + '}', 'values': values}

    up = [metric('up', dict(cluster=c, **labels), '1+0x10') for c, _, labels in targets]
    remote = [metric('prometheus_remote_storage_queue_highest_sent_timestamp_seconds', dict(cluster=c, **labels), '0+60x10') for c, _, labels in paths]
    rule = [metric('cortex_prometheus_rule_group_last_evaluation_timestamp_seconds', {'cluster': 'natsume', 'job': 'mimir', 'rule_group': f'data-ruler/anonymous/{c}%2F{ns}%2F{resource}%2Fuid;{group}'}, '0+60x10') for c, ns, resource, group in groups]
    healthy = up + remote + rule
    tests = []

    def case(name, inputs, checks):
        tests.append({'name': name, 'interval': '1m', 'input_series': inputs, 'promql_expr_test': checks})

    case('healthy groups for BOTH clusters evaluated on natsume', healthy,
         [check(i, 0, c) for c in hosts for i in [2, 3, 4, 5]] +
         [check(21, sum(g[0] == c for g in groups), c, wrapper='count(({expr}) >= 0)') for c in hosts])
    case('all series absent keeps every expected entity', [],
         [check(2, sum(t[0] == c for t in targets), c) for c in hosts] +
         [check(3, 0, c) for c in hosts] +
         [check(4, sum(p[0] == c for p in paths), c) for c in hosts] +
         [check(5, sum(g[0] == c for g in groups), c) for c in hosts])
    down = copy.deepcopy(healthy)
    down[0]['values'] = '0+0x10'
    first_cluster = targets[0][0]
    case('scrape failure differs from disappearance', down, [check(2, 0, first_cluster), check(3, 1, first_cluster)])
    stale = copy.deepcopy(healthy)
    stale[0]['values'] = '1 _ _ _ _ _ _ _ _ _ _'
    case('old up=1 is not current success', stale, [check(2, 1, first_cluster), check(3, 0, first_cluster)])
    stalled = copy.deepcopy(healthy)
    stalled[len(up)]['values'] = '0+0x10'
    case('fresh telemetry reports stalled remote write', stalled, [check(4, 1, paths[0][0], time='10m')])
    no_remote = copy.deepcopy(healthy)
    del no_remote[len(up)]
    case('missing remote path stays in inventory', no_remote, [check(4, 1, paths[0][0])])
    no_rule = copy.deepcopy(healthy)
    missing_index = next(i for i, g in enumerate(groups) if g[0] == 'meruto')
    del no_rule[len(up) + len(remote) + missing_index]
    case('same-named other cluster group does not hide missing meruto rule', no_rule, [check(5, 1, 'meruto'), check(5, 0, 'natsume')])
    stalled_rule = copy.deepcopy(healthy)
    stalled_rule[len(up) + len(remote) + missing_index]['values'] = '0+0x10'
    case('rule telemetry present but evaluation stopped', stalled_rule, [check(5, 1, 'meruto', time='10m'), check(5, 0, 'natsume', time='10m')])
    case('historical slack traffic and webhook reset are not current sends', [
        metric('cortex_alertmanager_notifications_total', {'cluster': 'natsume', 'job': 'mimir', 'integration': 'slack'}, '10+100x5'),
        metric('cortex_alertmanager_notifications_total', {'cluster': 'natsume', 'job': 'mimir', 'integration': 'webhook'}, '100 100 0 0 0 0'),
    ], [check(37, 0, 'natsume'), check(37, 0, 'meruto')])
    case('missing notification counters are not successful zero', [], [check(36, None), check(37, None), check(38, None)])
    payload = yaml.safe_dump({'rule_files': [], 'evaluation_interval': '1m', 'tests': tests})
    subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', '/bin/sh', 'prom/prometheus:v3.5.0', '-c', 'cat > /tmp/test.yaml && promtool test rules /tmp/test.yaml'], input=payload, text=True, check=True)
    print(f'Pipeline: {len(tests)} semantic cases passed')


if __name__ == '__main__':
    main()

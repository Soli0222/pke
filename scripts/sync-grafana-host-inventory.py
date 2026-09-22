#!/usr/bin/env python3
"""ホスト画面の期待対象をinventoryから更新し、監視ルールとの一致を検証する。"""
import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = Path('grafana/dashboards/platform/host-systemd.json')
KEYS = 'cluster,hostname,name'
SCOPE = 'cluster="$cluster",hostname="$hostname",job="integrations/unix"'


def inventory(root=ROOT):
    source = yaml.safe_load((root / 'ansible/inventories/hosts.yaml').read_text())
    groups = {}

    def register(name, value):
        groups[name] = value or groups.get(name, {})
        for child, data in (value or {}).get('children', {}).items():
            register(child, data)

    register('all', source['all'])

    def members(name):
        group = groups[name]
        result = set(group.get('hosts', {}))
        for child in group.get('children', {}):
            result |= members(child)
        return result

    expected = {}
    for host in sorted(members('k3s_cluster')):
        values = yaml.safe_load((root / f'ansible/inventories/host_vars/{host}.yaml').read_text())
        cluster, units = values['cluster'], values['alloy_systemd_units']
        if not units or len(units) != len(set(units)) or not all(isinstance(x, str) and x.endswith('.service') for x in units):
            raise ValueError(f'{host}: alloy_systemd_units must contain unique service names')
        expected.setdefault(cluster, {})[host] = sorted(units)
    monitored = {}
    for path in sorted((root / 'flux/clusters').glob('*/apps/monitoring-rules/helmrelease-monitoring-rules.yaml')):
        hosts = yaml.safe_load(path.read_text())['spec']['values'].get('hosts', [])
        cluster = path.parents[2].name
        monitored[cluster] = {h['name']: sorted(h['units']) for h in hosts}
        if len(monitored[cluster]) != len(hosts):
            raise ValueError(f'{cluster}: duplicate host in monitoring rules')
    if monitored != expected:
        raise ValueError('AnsibleのK3s hosts / alloy_systemd_unitsと監視ルールのhosts / unitsが一致しません')
    return expected


def labelled(labels):
    expr = 'vector(1)'
    for key, value in labels.items():
        expr = f'label_replace({expr}, {json.dumps(key)}, {json.dumps(value)}, "", "")'
    return expr


def expressions(expected):
    hosts, units = [], []
    for cluster, entries in sorted(expected.items()):
        for hostname, names in sorted(entries.items()):
            labels = {'cluster': cluster, 'hostname': hostname}
            hosts.append(labelled(labels))
            units.extend(labelled(dict(labels, name=name)) for name in names)
    host_set, unit_set = '(' + ' or '.join(hosts) + ')', '(' + ' or '.join(units) + ')'
    selection = labelled({'cluster': '$cluster', 'hostname': '$hostname'})
    selected_host = f'({host_set} and on(cluster,hostname) {selection})'
    selected_units = f'({unit_set} and on(cluster,hostname) {selection})'
    # Exactly one state must be active. Missing or inconsistent one-hot states are unknown.
    present = f'max by ({KEYS},state) (node_systemd_unit_state{{{SCOPE}}}) == 1'
    consistent = f'(sum by ({KEYS}) ({present}) == 1)'
    parts = []
    for state, code in [('active', 1), ('inactive', 2), ('failed', 3), ('activating', 4), ('deactivating', 5)]:
        parts.append(f'(max by ({KEYS}) (node_systemd_unit_state{{{SCOPE},state="{state}"}} == 1) * {code})')
    observed = f'(({" or ".join(parts)}) and on({KEYS}) {consistent})'
    status = f'(({observed} and on({KEYS}) {selected_units}) or on({KEYS}) ({selected_units} * 0))'
    return {
        'hostname': 'query_result(' + host_set + ' and on(cluster) ' + labelled({'cluster': '$cluster'}) + ')',
        1: f'max(up{{{SCOPE}}}) or max({selected_host} * -1)',
        2: f'sum({status} == bool 0)',
        3: f'sum({status} == bool 3)',
        4: f'sum({status} == bool 2)',
        5: status,
        6: status,
    }


def synchronize(document, expected):
    queries = expressions(expected)
    variables = {v['name']: v for v in document['spec']['templating']['list']}
    clusters = sorted(expected)
    variables['cluster']['query'] = ','.join(clusters)
    if variables['cluster']['current']['value'] not in expected:
        variables['cluster']['current'] = {'text': clusters[0], 'value': clusters[0]}
    variables['cluster']['options'] = [{'text': c, 'value': c, 'selected': c == variables['cluster']['current']['value']} for c in clusters]
    cluster = variables['cluster']['current']['value']
    if variables['hostname']['current']['value'] not in expected[cluster]:
        host = sorted(expected[cluster])[0]
        variables['hostname']['current'] = {'text': host, 'value': host}
    variables['hostname']['query'] = {'query': queries.pop('hostname'), 'refId': 'StandardVariableQuery'}
    for panel in document['spec']['panels']:
        if panel['id'] in queries:
            panel['targets'][0]['expr'] = queries[panel['id']]
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='差分があれば失敗し、ファイルは変更しない')
    args = parser.parse_args()
    path = ROOT / DASHBOARD
    source = path.read_text()
    expected = inventory()
    rendered = json.dumps(synchronize(json.loads(source), expected), ensure_ascii=False, indent=2) + '\n'
    if args.check and source != rendered:
        raise SystemExit('Host dashboard is stale: run python3 scripts/sync-grafana-host-inventory.py')
    if not args.check:
        path.write_text(rendered)
    print(f'Host inventory: {sum(len(h) for h in expected.values())} hosts; monitoring rules and dashboard agree.')


if __name__ == '__main__':
    main()

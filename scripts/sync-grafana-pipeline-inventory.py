#!/usr/bin/env python3
"""Monitoring Pipelineの期待対象をinventory・Helmの設定から生成する。"""
import argparse
import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = Path('grafana/dashboards/observability/monitoring-pipeline.json')


def load_host_inventory(root):
    spec = importlib.util.spec_from_file_location('host_inventory', ROOT / 'scripts/sync-grafana-host-inventory.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inventory(root)


def label(expr, labels):
    for key, value in labels.items():
        expr = f'label_replace({expr}, {json.dumps(key)}, {json.dumps(value)}, "", "")'
    return expr


def select(parts):
    return '(' + ' or '.join(parts) + ') and on(cluster) ' + label('vector(1)', {'cluster': '$cluster'})


def expected(root=ROOT):
    hosts = load_host_inventory(root)
    targets, paths, groups = [], [], []
    for cluster, nodes in sorted(hosts.items()):
        for title, job in [('Alloy / Kubernetes', 'alloy'), ('Kubernetes API', 'prometheus.scrape.kubernetes_api'), ('kube-state-metrics', 'kube-state-metrics')]:
            targets.append((cluster, title, {'job': job}))
        monitor = root / f'flux/clusters/{cluster}/apps/coredns-monitoring/servicemonitor-coredns.yaml'
        if monitor.exists():
            document = yaml.safe_load(monitor.read_text())
            endpoint = document['spec']['endpoints'][0]
            if not any(r.get('targetLabel') == 'job' and r.get('replacement') == 'coredns' for r in endpoint.get('relabelings', [])):
                raise ValueError('CoreDNS ServiceMonitor must use job=coredns')
            targets.append((cluster, 'CoreDNS', {'job': 'coredns'}))
        paths.append((cluster, 'Alloy / Kubernetes', {'job': 'alloy'}))
        for host, units in sorted(nodes.items()):
            for title, job in [('Alloy', 'alloy-host'), ('Node exporter', 'integrations/unix'), ('Kubelet', 'prometheus.scrape.kubernetes_nodes'), ('cAdvisor', 'prometheus.scrape.kubernetes_cadvisor')]:
                targets.append((cluster, f'{title} / {host}', {'job': job, 'instance': host}))
            paths.append((cluster, f'Alloy / {host}', {'job': 'alloy-host', 'instance': host}))
            if 'etcd.service' in units:
                targets.append((cluster, f'etcd / {host}', {'job': 'etcd', 'instance': host}))
        # Local charts: rendered groups follow enabled rules, not a copied count.
        for chart in ['monitoring-rules', 'blackbox-exporter-probes']:
            path = root / f'flux/clusters/{cluster}/apps/{chart}/helmrelease-{chart}.yaml'
            if not path.exists():
                continue
            release = yaml.safe_load(path.read_text())
            namespace = release['spec'].get('targetNamespace', release['metadata']['namespace'])
            name = release['spec'].get('releaseName', release['metadata']['name'])
            with tempfile.TemporaryDirectory(prefix='pke-pipeline-') as tmp:
                values = Path(tmp) / 'values.yaml'
                values.write_text(yaml.safe_dump(release['spec'].get('values', {})))
                output = subprocess.check_output(['helm', 'template', name, str(root / 'charts' / chart), '-n', namespace, '-f', str(values)], text=True)
            for doc in yaml.safe_load_all(output):
                if doc and doc.get('kind') == 'PrometheusRule':
                    groups.extend((cluster, namespace, doc['metadata']['name'], g['name']) for g in doc['spec']['groups'])
    # Upstream chart group names are pinned in the expectation registry. The
    # enable flag is checked locally; names must also be checked on chart updates.
    registry = yaml.safe_load((root / 'grafana/pipeline-rule-groups.yaml').read_text())
    for entry in registry['upstreamGroups']:
        if Path(entry['source']).parts[2] != entry['cluster']:
            raise ValueError('Rule group cluster differs from its source')
        release = yaml.safe_load((root / entry['source']).read_text())
        enabled = release['spec'].get('values', {})
        for key in entry['enabledPath'].split('.'):
            enabled = enabled[key]
        if enabled:
            groups.append((entry['cluster'], release['metadata']['namespace'], entry['resource'], entry['group']))
    if len(groups) != len(set(groups)):
        raise ValueError('Duplicate expected rule group')
    return hosts, targets, paths, sorted(groups)


def selector(cluster, labels):
    return ','.join(f'{k}={json.dumps(v)}' for k, v in dict(cluster=cluster, **labels).items())


def expressions(targets, paths, groups):
    states, ages, evaluations = [], [], []
    for cluster, title, labels in targets:
        series = 'up{' + selector(cluster, labels) + '}'
        # A cached last up=1 is not evidence of current collection.
        state = f'min({series} and (timestamp({series}) > time() - 120)) or vector(-1)'
        states.append(label(state, {'cluster': cluster, 'target': title}))
    for cluster, title, labels in paths:
        metric = 'prometheus_remote_storage_queue_highest_sent_timestamp_seconds{' + selector(cluster, labels) + '}'
        age = f'max(time() - ({metric} and (timestamp({metric}) > time() - 120))) or vector(-1)'
        ages.append(label(age, {'cluster': cluster, 'target': title}))
    for cluster, namespace, resource, group in groups:
        pattern = f'.*/{cluster}%2F{namespace}%2F{resource}%2F[^;]+;{re.escape(group)}'
        metric = 'cortex_prometheus_rule_group_last_evaluation_timestamp_seconds{cluster="natsume",job="mimir",rule_group=~' + json.dumps(pattern) + '}'
        age = f'(time() - max({metric} and (timestamp({metric}) > time() - 120))) or vector(-1)'
        evaluations.append(label(age, {'cluster': cluster, 'group': f'{namespace} / {resource} / {group}'}))
    states, ages, evaluations = select(states), select(ages), select(evaluations)
    return {
        2: f'sum(({states}) == bool -1)',
        3: f'sum(({states}) == bool 0)',
        4: f'sum((({ages}) == bool -1) + (({ages}) > bool 300))',
        5: f'sum((({evaluations}) == bool -1) + (({evaluations}) > bool 300))',
        10: states,
        12: ages,
        21: evaluations,
    }


def synchronize(document, data):
    hosts, targets, paths, groups = data
    variable = document['spec']['templating']['list'][0]
    variable['query'] = ','.join(sorted(hosts))
    current = variable['current']['value']
    if current not in hosts:
        current = sorted(hosts)[0]
        variable['current'] = {'text': current, 'value': current}
    variable['options'] = [{'text': c, 'value': c, 'selected': c == current} for c in sorted(hosts)]
    queries = expressions(targets, paths, groups)
    for panel in document['spec']['panels']:
        if panel['id'] in queries:
            panel['targets'][0]['expr'] = queries[panel['id']]
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / DASHBOARD
    original = path.read_text()
    data = expected()
    rendered = json.dumps(synchronize(json.loads(original), data), ensure_ascii=False, indent=2) + '\n'
    if args.check and original != rendered:
        raise SystemExit('Pipeline inventory is stale: run python3 scripts/sync-grafana-pipeline-inventory.py')
    if not args.check:
        path.write_text(rendered)
    print(f'Pipeline inventory: {len(data[1])} collection paths, {len(data[2])} Alloy paths, {len(data[3])} rule groups')


if __name__ == '__main__':
    main()

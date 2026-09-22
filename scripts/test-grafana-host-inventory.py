#!/usr/bin/env python3
"""期待対象の設定ずれと欠測時のPromQLを、実環境に書き込まず検証する。"""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location('host_inventory', Path(__file__).with_name('sync-grafana-host-inventory.py'))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class InventoryTest(unittest.TestCase):
    def test_inventory_and_rules_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                'ansible/inventories/hosts.yaml': {'all': {'children': {'k3s_cluster': {'hosts': {'test-host': None}}}}},
                'ansible/inventories/host_vars/test-host.yaml': {'cluster': 'natsume', 'alloy_systemd_units': ['alloy.service']},
                'flux/clusters/natsume/apps/monitoring-rules/helmrelease-monitoring-rules.yaml': {'spec': {'values': {'hosts': [{'name': 'test-host', 'units': ['alloy.service']}]}}},
            }
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(yaml.safe_dump(data))
            self.assertEqual(host.inventory(root), {'natsume': {'test-host': ['alloy.service']}})
            path = root / 'ansible/inventories/host_vars/test-host.yaml'
            path.write_text(yaml.safe_dump({'cluster': 'natsume', 'alloy_systemd_units': ['alloy.service', 'new.service']}))
            with self.assertRaises(ValueError):
                host.inventory(root)

    def test_state_and_absence_with_promtool(self):
        names = ['active.service', 'inactive.service', 'failed.service', 'missing.service', 'conflict.service', 'zero.service']
        queries = host.expressions({'natsume': {'test-host': names}, 'meruto': {'other-host': ['alloy.service']}})
        queries = {k: v.replace('$cluster', 'natsume').replace('$hostname', 'test-host') for k, v in queries.items()}
        def sample(name, state, value, cluster='natsume'):
            return {'series': f'node_systemd_unit_state{{cluster="{cluster}",hostname="test-host",job="integrations/unix",name="{name}",state="{state}"}}', 'values': f'{value}+0x5'}
        rows = [sample('active.service', 'active', 1), sample('inactive.service', 'inactive', 1), sample('failed.service', 'failed', 1), sample('conflict.service', 'active', 1), sample('conflict.service', 'failed', 1), sample('zero.service', 'active', 0), sample('active.service', 'failed', 1, 'meruto')]
        rows.append({'series': 'up{cluster="natsume",hostname="test-host",job="integrations/unix"}', 'values': '1+0x5'})
        def table(values):
            return [{'labels': '{cluster="natsume",hostname="test-host",name=' + json.dumps(name) + '}', 'value': value} for name, value in zip(names, values)]
        def checks(stats, values):
            return [{'expr': queries[pid], 'eval_time': '5m', 'exp_samples': [{'labels': '{}', 'value': value}]} for pid, value in zip([1, 2, 3, 4], stats)] + [{'expr': queries[5], 'eval_time': '5m', 'exp_samples': table(values)}]
        tests = [
            {'name': 'active inactive failed missing conflicting and foreign cluster', 'interval': '1m', 'input_series': rows, 'promql_expr_test': checks([1, 3, 1, 1], [1, 2, 3, 0, 0, 0])},
            {'name': 'all metrics missing keeps every expected unit', 'interval': '1m', 'input_series': [], 'promql_expr_test': checks([-1, 6, 0, 0], [0] * 6)},
            {'name': 'scrape failure differs from absent host', 'interval': '1m', 'input_series': [{'series': 'up{cluster="natsume",hostname="test-host",job="integrations/unix"}', 'values': '0+0x5'}], 'promql_expr_test': checks([0, 6, 0, 0], [0] * 6)},
        ]
        # Send fixture through stdin: no Docker host bind path assumptions on macOS/CI.
        payload = yaml.safe_dump({'rule_files': [], 'evaluation_interval': '1m', 'tests': tests})
        subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', '/bin/sh', 'prom/prometheus:v3.5.0', '-c', 'cat > /tmp/test.yaml && promtool test rules /tmp/test.yaml'], input=payload, text=True, check=True)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""認証なしのCIで、保存形式と危険な参照ミスの拒否を確かめる。"""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('validator', Path(__file__).with_name('validate-grafana-dashboards.py'))
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
DS = 'test-prometheus'
V1 = {
    'apiVersion': 'dashboard.grafana.app/v1', 'kind': 'Dashboard',
    'metadata': {'name': 'test-dashboard'},
    'spec': {'title': 'Test dashboard', 'schemaVersion': 42,
             'time': {'from': 'now-6h', 'to': 'now'},
             'panels': [{'id': 1, 'type': 'timeseries',
                         'datasource': {'type': 'prometheus', 'uid': DS},
                         'targets': [{'refId': 'A', 'expr': 'up{cluster="$cluster"}'}]}]}}
V2 = {
    'apiVersion': 'dashboard.grafana.app/v2', 'kind': 'Dashboard',
    'metadata': {'name': 'test-dashboard'},
    'spec': {'title': 'Test dashboard', 'timeSettings': {'from': 'now-6h', 'to': 'now'},
             'variables': [], 'elements': {'panel-1': {'kind': 'Panel', 'spec': {
                 'id': 1, 'title': 'Scrape', 'vizConfig': {}, 'data': {
                     'kind': 'DataQuery', 'group': 'prometheus',
                     'datasource': {'name': DS}, 'spec': {'expr': 'up'}}}}},
             'layout': {'kind': 'GridLayout', 'spec': {'items': [
                 {'kind': 'ElementReference', 'name': 'panel-1'}]}}}}


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'dashboards/platform').mkdir(parents=True)
        self.catalog = {'datasources': {DS: {'name': 'Mimir', 'type': 'prometheus'}},
                        'dashboards': {'test-dashboard': {
                            'path': 'dashboards/platform/test.json', 'title': 'Test dashboard',
                            'origin': {'kind': 'custom', 'url': 'https://github.com/Soli0222/pke',
                                       'revision': 'git', 'license': 'repository',
                                       'changes': ['PKE original']}}}}

    def check(self, resource, expected=None, raw=None):
        (self.root / 'catalog.json').write_text(json.dumps(self.catalog))
        (self.root / 'dashboards/platform/test.json').write_text(raw if raw is not None else json.dumps(resource))
        errors, count = validator.validate(self.root)
        if expected is None:
            self.assertEqual(errors, [])
            self.assertEqual(count, 1)
        else:
            self.assertTrue(any(expected in e for e in errors), errors)

    def test_valid_v1_v2(self):
        for resource in (V1, V2):
            with self.subTest(version=resource['apiVersion']):
                self.check(resource)

    def test_invalid_json_and_nonfinite(self):
        for raw in ('{', '{"kind":"Dashboard","kind":"Dashboard"}', '{"value":NaN}'):
            with self.subTest(raw=raw):
                self.check(None, 'invalid JSON', raw)

    def test_duplicate_uid(self):
        (self.root / 'dashboards/duplicate.json').write_text(json.dumps(V1))
        self.check(V1, 'duplicate dashboard UID')

    def test_wrong_shape(self):
        resource = copy.deepcopy(V2)
        resource['spec']['elements'] = []
        self.check(resource, 'schema type')

    def test_api_version_mismatch(self):
        resource = copy.deepcopy(V1)
        resource['apiVersion'] = 'dashboard.grafana.app/v2'
        self.check(resource, 'schema required')

    def test_runtime_metadata(self):
        resource = copy.deepcopy(V1)
        resource['metadata']['resourceVersion'] = '123'
        self.check(resource, 'schema additionalProperties')

    def test_datasource_uid_and_type(self):
        for value in ({'uid': 'unknown', 'type': 'prometheus'}, {'uid': DS, 'type': 'loki'}, None):
            resource = copy.deepcopy(V1)
            resource['spec']['panels'][0]['datasource'] = value
            self.check(resource, 'datasource')

    def test_v2_datasource_name(self):
        resource = copy.deepcopy(V2)
        resource['spec']['elements']['panel-1']['spec']['data']['datasource']['name'] = 'unknown'
        self.check(resource, 'unknown datasource UID')

    def test_datasource_variable(self):
        resource = copy.deepcopy(V1)
        resource['spec']['panels'][0]['datasource']['uid'] = '${ds}'
        self.check(resource, 'undeclared datasource variable')
        resource['spec']['templating'] = {'list': [{'name': 'ds', 'type': 'datasource', 'query': 'prometheus'}]}
        self.check(resource)

    def test_placeholder(self):
        resource = copy.deepcopy(V1)
        resource['spec']['panels'][0]['datasource']['uid'] = '${DS_PROMETHEUS}'
        self.check(resource, 'unresolved import placeholder')

    def test_builtin_annotations_keep_grafana_datasource(self):
        resource = copy.deepcopy(V1)
        annotation = {'builtIn': 1, 'type': 'dashboard',
                      'datasource': {'uid': 'grafana', 'type': 'datasource'}}
        resource['spec']['annotations'] = {'list': [annotation]}
        self.check(resource)
        annotation['datasource'] = {'uid': DS, 'type': 'prometheus'}
        self.check(resource, 'built-in annotations must use Grafana datasource')

    def test_broken_layout_reference(self):
        resource = copy.deepcopy(V2)
        resource['spec']['layout']['spec']['items'][0]['name'] = 'missing'
        self.check(resource, 'unknown layout element')

    def test_duplicate_panel_in_collapsed_row(self):
        resource = copy.deepcopy(V1)
        resource['spec']['panels'].append({'id': 2, 'type': 'row', 'panels': [copy.deepcopy(resource['spec']['panels'][0])]})
        self.check(resource, 'duplicate panel ID')

    def test_unpinned_upstream(self):
        self.catalog['dashboards']['test-dashboard']['origin'].update(kind='upstream', revision='main')
        self.check(V1, 'pinned revision')

    def test_missing_catalog(self):
        self.catalog['dashboards'] = {}
        self.check(V1, 'catalog UID/path/title')

    def test_catalog_missing_file(self):
        self.catalog['dashboards']['missing'] = copy.deepcopy(self.catalog['dashboards']['test-dashboard'])
        self.check(V1, 'dashboard missing is missing')

    def test_unsupported_sync_file(self):
        (self.root / 'dashboards/backup.yaml').write_text('kind: Dashboard')
        self.check(V1, 'only regular dashboard JSON')


if __name__ == '__main__':
    unittest.main()

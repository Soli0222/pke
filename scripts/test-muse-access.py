#!/usr/bin/env python3
"""muse の RBAC 境界と kubeconfig 出力の秘密情報保護を検証する。"""
import base64
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('export_muse', ROOT / 'scripts/export-muse-kubeconfig.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def fake_token(**extra):
    claims = {'sub': 'system:serviceaccount:muse-system:muse-observer', **extra}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return base64.b64encode(f'header.{payload}.signature'.encode()).decode()


class KubeconfigTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            'server': 'https://example.test:6443',
            'certificate-authority-data': 'test-ca',
            'proxy-url': 'http://must-not-be-copied.test',
            'extensions': [{'name': 'must-not-be-copied'}],
        }
        self.account = {'metadata': {'uid': 'test-uid'}}
        self.secret = {
            'type': 'kubernetes.io/service-account-token',
            'metadata': {
                'name': 'muse-observer-token', 'namespace': 'muse-system',
                'annotations': {
                    'kubernetes.io/service-account.name': 'muse-observer',
                    'kubernetes.io/service-account.uid': 'test-uid',
                },
            },
            'data': {'token': fake_token()},
        }

    def config(self, **kwargs):
        return EXPORT.make_config('meruto', self.source, self.secret, self.account, **kwargs)

    def test_only_observer_credentials_and_verified_transport_are_exported(self):
        config = self.config(server='https://100.64.0.1:6443', tls_name='192.0.2.1')
        self.assertEqual(config['clusters'][0]['cluster'], {
            'server': 'https://100.64.0.1:6443',
            'certificate-authority-data': 'test-ca', 'tls-server-name': '192.0.2.1',
        })
        self.assertEqual(set(config['users'][0]['user']), {'token'})
        self.assertEqual(config['current-context'], 'meruto@muse')
        self.assertEqual(len(config['contexts']), 1)

    def test_insecure_or_embedded_credentials_in_endpoint_are_rejected(self):
        for server in ['http://example.test:6443', 'https://user:password@example.test:6443',
                       'https://example.test:443', 'https://example.test:6443?token=test']:
            with self.subTest(server=server), self.assertRaises(ValueError):
                self.config(server=server)
        self.source['insecure-skip-tls-verify'] = True
        with self.assertRaises(ValueError):
            self.config()

    def test_mismatched_or_expiring_credentials_are_rejected(self):
        self.secret['data']['token'] = fake_token(exp=9999999999)
        with self.assertRaises(ValueError):
            self.config()
        self.secret['data']['token'] = fake_token(sub='system:serviceaccount:default:admin')
        with self.assertRaises(ValueError):
            self.config()
        self.secret['data']['token'] = fake_token()
        self.account['metadata']['uid'] = 'recreated-account-uid'
        with self.assertRaises(ValueError):
            self.config()

    def test_auto_generated_token_classification_is_rejected(self):
        self.account['secrets'] = [{'name': 'muse-observer-token'}]
        with self.assertRaises(ValueError):
            self.config()

    def test_output_is_private_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = EXPORT.output_path(str(Path(directory) / 'config'))
            EXPORT.write_config(path, self.config())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                EXPORT.write_config(path, {})
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(ValueError):
                EXPORT.output_path(str(path))
            link = Path(directory) / 'link'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                EXPORT.output_path(str(link))
            Path(directory).chmod(0o755)
            with self.assertRaises(ValueError):
                EXPORT.output_path(str(Path(directory) / 'another-config'))
        with self.assertRaises(ValueError):
            EXPORT.output_path(str(ROOT / 'muse-config-must-not-exist'))


class RBACTests(unittest.TestCase):
    def test_rendered_permissions_for_both_clusters(self):
        for cluster in ['natsume', 'meruto']:
            with self.subTest(cluster=cluster):
                result = subprocess.run(
                    ['kustomize', 'build', str(ROOT / f'flux/clusters/{cluster}/apps/muse-access')],
                    capture_output=True, text=True, check=True,
                )
                docs = list(yaml.safe_load_all(result.stdout))
                role = next(d for d in docs if d['kind'] == 'ClusterRole')
                self.assertNotIn('aggregationRule', role)
                for rule in role['rules']:
                    self.assertLessEqual(set(rule['verbs']), {'get', 'list', 'watch'})
                    for field in ['apiGroups', 'resources', 'nonResourceURLs']:
                        self.assertTrue(all('*' not in value for value in rule.get(field, [])))
                    if '' in rule.get('apiGroups', []):
                        self.assertFalse(set(rule['resources']) & {
                            'secrets', 'configmaps', 'pods/exec', 'pods/attach',
                            'pods/portforward', 'pods/proxy', 'nodes/proxy',
                            'services/proxy', 'serviceaccounts/token',
                        })
                cnpg = any('postgresql.cnpg.io' in rule.get('apiGroups', []) for rule in role['rules'])
                self.assertEqual(cnpg, cluster == 'natsume')
                self.assertTrue(any('pods/log' in rule.get('resources', []) for rule in role['rules']))
                secret = next(d for d in docs if d['kind'] == 'Secret')
                self.assertNotIn('data', secret)
                self.assertNotIn('stringData', secret)
                account = next(d for d in docs if d['kind'] == 'ServiceAccount')
                self.assertNotIn('secrets', account)
                self.assertFalse(account['automountServiceAccountToken'])
                binding = next(d for d in docs if d['kind'] == 'ClusterRoleBinding')
                self.assertEqual(binding['roleRef']['name'], role['metadata']['name'])
                self.assertEqual(binding['subjects'], [{
                    'kind': 'ServiceAccount', 'name': 'muse-observer', 'namespace': 'muse-system',
                }])


if __name__ == '__main__':
    unittest.main()

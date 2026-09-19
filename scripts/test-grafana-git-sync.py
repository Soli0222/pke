#!/usr/bin/env python3
"""Git Sync初回登録のwrite境界と秘密鍵の扱いを検証する。外部接続はしない。"""
import contextlib
import base64
import importlib.util
import io
import json
import os
import stat
import unittest
from pathlib import Path
from unittest.mock import patch

module = importlib.util.spec_from_file_location('configure', Path(__file__).with_name('configure-grafana-git-sync.py'))
configure = importlib.util.module_from_spec(module)
module.loader.exec_module(configure)
FAKE_KEY = '-----BEGIN PRIVATE KEY-----\nTEST-ONLY-NOT-A-REAL-KEY\n-----END PRIVATE KEY-----'


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.secret_paths = []
        self.existing = False
        self.attachment = False

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == 'op':
            self.assertTrue(kwargs['sensitive'])
            if args[1] == 'read':
                self.assertTrue(args[2].endswith('/private-key.pem'))
                return FAKE_KEY
            return json.dumps({'fields': [{'label': k, 'value': v} for k, v in
                [('app-id', '123'), ('installation-id', '456'), ('private-key', 'broken' if self.attachment else FAKE_KEY)]],
                'files': [{'name': 'private-key.pem'}] if self.attachment else []})
        if 'get' in args:
            return json.dumps({'items': [{'metadata': {'name': 'pke-github'}}] if self.existing else []})
        if '-p' in args:
            path = Path(args[args.index('-p') + 1])
            data = json.loads(path.read_text())
            if data['kind'] == 'Connection':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertTrue(kwargs['sensitive'])
                self.assertEqual(base64.b64decode(data['secure']['privateKey']['create']).decode(), FAKE_KEY)
                self.secret_paths.append(path)
        return '{}'

    def invoke(self, apply=False):
        output = io.StringIO()
        argv = ['configure'] + (['--apply'] if apply else [])
        with patch.object(configure, 'run', self.fake_run), patch('sys.argv', argv), contextlib.redirect_stdout(output):
            configure.main()
        self.assertNotIn(FAKE_KEY, output.getvalue())
        self.assertNotIn(base64.b64encode(FAKE_KEY.encode()).decode(), output.getvalue())
        self.assertTrue(self.secret_paths)
        self.assertTrue(all(not p.exists() for p in self.secret_paths))
        return [c for c in self.calls if 'push' in c and '--dry-run' not in c]

    def test_default_does_not_write(self):
        self.assertEqual(self.invoke(), [])

    def test_attachment_takes_precedence_without_losing_newlines(self):
        self.attachment = True
        self.assertEqual(self.invoke(), [])
        self.assertTrue(any(c[:2] == ['op', 'read'] for c in self.calls))

    def test_apply_writes_only_connection_then_repository(self):
        writes = self.invoke(apply=True)
        self.assertEqual(len(writes), 2)
        self.assertTrue(writes[0][-1].endswith('connection.json'))
        self.assertTrue(writes[1][-1].endswith('repository.json'))

    def test_existing_connection_stops_before_reading_secret(self):
        self.existing = True
        with patch.object(configure, 'run', self.fake_run), patch('sys.argv', ['configure', '--apply']):
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                configure.main()
        self.assertFalse(any(c[0] == 'op' for c in self.calls))
        self.assertFalse(any('push' in c for c in self.calls))

    def test_repository_scope_and_workflow(self):
        resource = json.loads((configure.ROOT / 'grafana/git-sync/repository.json').read_text())
        spec = resource['spec']
        self.assertEqual(spec['github']['url'], 'https://github.com/Soli0222/pke')
        self.assertEqual(spec['github']['branch'], 'main')
        self.assertEqual(spec['github']['path'], 'grafana/dashboards/')
        self.assertEqual(spec['sync']['target'], 'folder')
        self.assertEqual(spec['workflows'], ['branch'])
        self.assertTrue(spec['webhook']['disabled'])
        self.assertNotIn('secure', resource)


if __name__ == '__main__':
    unittest.main()

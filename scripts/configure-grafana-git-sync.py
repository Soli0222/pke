#!/usr/bin/env python3
"""1Passwordの専用GitHub Appをgcx経由でGrafanaへ登録する。既定はdry-run。"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, sensitive=False):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        # secureフィールドを含むdiff/API応答をログへ出さない。
        if not sensitive:
            print(result.stderr.strip(), file=sys.stderr)
        raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode})')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', default='default')
    parser.add_argument('--item', default='grafana-git-sync')
    parser.add_argument('--vault', default='Kubernetes')
    parser.add_argument('--apply', action='store_true', help='dry-run後にConnectionとRepositoryを作成する')
    args = parser.parse_args()
    gcx = ['gcx', '--context', args.context]
    run(gcx + ['config', 'check'])
    # 初回作成専用。既存接続やSecretの不用意な再登録・上書きを防ぐ。
    for plural, name in [('connections', 'pke-github'), ('repositories', 'pke-dashboards')]:
        current = json.loads(run(gcx + ['resources', 'get', plural, '-o', 'json'], sensitive=True))
        if any(r['metadata']['name'] == name for r in current.get('items', [])):
            raise RuntimeError(f'{name} already exists; inspect with gcx before changing it')
    item = json.loads(run(['op', 'item', 'get', args.item, '--vault', args.vault, '--format', 'json'], sensitive=True))
    fields = {f.get('label'): f.get('value', '') for f in item.get('fields', [])}
    app_id, installation_id = fields.get('app-id', ''), fields.get('installation-id', '')
    private_key = fields.get('private-key', '')
    if not app_id.isdecimal() or not installation_id.isdecimal():
        raise RuntimeError('app-id and installation-id must be numeric 1Password fields')
    if 'PRIVATE KEY-----' not in private_key or '\n' not in private_key:
        raise RuntimeError('private-key must contain the complete multiline PEM')
    config = ROOT / 'grafana/git-sync'
    connection = json.loads((config / 'connection.template.json').read_text())
    connection['spec']['github'] = {'appID': app_id, 'installationID': installation_id}
    connection['secure'] = {'privateKey': {'create': private_key}}
    # git管理外の0700ディレクトリ / 0600ファイルにのみ一時保存する。
    with tempfile.TemporaryDirectory(prefix='pke-grafana-git-sync-') as temp:
        path = Path(temp) / 'connection.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(connection, output)
        run(gcx + ['resources', 'validate', '-p', str(path), '-o', 'json'], sensitive=True)
        run(gcx + ['resources', 'push', '-p', str(path), '--dry-run'], sensitive=True)
        print('Connection preflight passed (secure output suppressed; server admission occurs on create).')
        if args.apply:
            run(gcx + ['resources', 'push', '-p', str(path)], sensitive=True)
            print('Created Connection pke-github.')
        repository = config / 'repository.json'
        run(gcx + ['resources', 'validate', '-p', str(repository), '-o', 'json'], sensitive=True)
        run(gcx + ['resources', 'push', '-p', str(repository), '--dry-run'], sensitive=True)
        print('Repository preflight passed (server admission occurs on create).')
        if args.apply:
            run(gcx + ['resources', 'push', '-p', str(repository)], sensitive=True)
            print('Created Repository pke-dashboards. Verify sync status and revision with gcx.')
    if not args.apply:
        print('No resources written. Use --apply after reviewing the committed configuration.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)

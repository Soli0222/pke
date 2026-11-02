#!/usr/bin/env python3
"""適用済みの muse-observer の期限なしトークンを専用 kubeconfig に保存する。

Python 3.9 以降と kubectl が必要。秘密値と kubectl の応答本文は表示しない。
"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


NAMESPACE = 'muse-system'
ACCOUNT = 'muse-observer'
TOKEN_SECRET = 'muse-observer-token'
REPOSITORY = Path(__file__).resolve().parent.parent


def kubectl_json(base, args):
    result = subprocess.run(base + args, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('kubectl failed: ' + ' '.join(args[:2]))
    return json.loads(result.stdout)


def make_config(cluster_name, source_cluster, secret, account, server=None, tls_name=None):
    if source_cluster.get('insecure-skip-tls-verify'):
        raise ValueError('管理 kubeconfig の TLS 検証が無効です')
    ca = source_cluster.get('certificate-authority-data')
    endpoint = server or source_cluster.get('server', '')
    url = urlsplit(endpoint)
    if (url.scheme != 'https' or not url.hostname or url.port != 6443
            or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/') or not ca):
        raise ValueError('CA と HTTPS の API endpoint（port 6443）が必要です')
    metadata = secret.get('metadata', {})
    annotations = metadata.get('annotations', {})
    if (secret.get('type') != 'kubernetes.io/service-account-token'
            or metadata.get('namespace') != NAMESPACE
            or metadata.get('name') != TOKEN_SECRET
            or annotations.get('kubernetes.io/service-account.name') != ACCOUNT
            or annotations.get('kubernetes.io/service-account.uid') != account['metadata']['uid']):
        raise ValueError('Secret と ServiceAccount の対応が不正です')
    if any(item.get('name') == TOKEN_SECRET for item in account.get('secrets', [])):
        raise ValueError('手動発行トークンを ServiceAccount.secrets に登録しないでください')
    token = base64.b64decode(secret['data']['token'], validate=True).decode('ascii')
    # これは発行形式の確認。署名・失効・権限は別途 API への接続で確認する。
    payload = token.split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    if 'exp' in claims or claims.get('sub') != f'system:serviceaccount:{NAMESPACE}:{ACCOUNT}':
        raise ValueError('muse-observer の期限なしトークンではありません')
    cluster = {'server': endpoint, 'certificate-authority-data': ca}
    tls_name = tls_name or source_cluster.get('tls-server-name')
    if tls_name:
        cluster['tls-server-name'] = tls_name
    context = cluster_name + '@muse'
    # 管理用の users、exec、proxy-url、client certificate 等は引き継がない。
    return {
        'apiVersion': 'v1', 'kind': 'Config',
        'clusters': [{'name': cluster_name, 'cluster': cluster}],
        'users': [{'name': context, 'user': {'token': token}}],
        'contexts': [{'name': context, 'context': {
            'cluster': cluster_name, 'user': context, 'namespace': 'flux-system',
        }}],
        'current-context': context,
    }


def output_path(value):
    requested = Path(value).expanduser().absolute()
    if requested.is_symlink():
        raise ValueError('symlink は出力先に指定できません')
    path = requested.resolve()
    if path == REPOSITORY or REPOSITORY in path.parents:
        raise ValueError('kubeconfig はリポジトリ外に保存してください')
    if path.exists():
        raise ValueError('出力先は既に存在します。既存ファイルは上書きしません')
    # 出力先の親は利用者が事前に作成する。広いディレクトリの権限は変更しない。
    if not path.parent.is_dir() or path.parent.stat().st_mode & 0o077:
        raise ValueError('出力先には事前に用意した mode 0700 のディレクトリが必要です')
    return path


def write_config(path, config):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(config, stream, indent=2)
            stream.write('\n')
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cluster', required=True, choices=['natsume', 'meruto'])
    parser.add_argument('--kubeconfig', help='管理 kubeconfig。省略時は kubectl の既定値')
    parser.add_argument('--server', help='muse から到達する HTTPS API endpoint')
    parser.add_argument('--tls-server-name', help='API 証明書の SAN にある DNS 名または IP')
    parser.add_argument('--output', required=True, help='リポジトリ外の新規ファイル')
    args = parser.parse_args()
    path = output_path(args.output)
    base = ['kubectl', '--context', args.cluster + '@soli', '--request-timeout=30s']
    if args.kubeconfig:
        base += ['--kubeconfig', args.kubeconfig]
    source = kubectl_json(base, ['config', 'view', '--minify', '--flatten', '--raw', '-o', 'json'])
    # server override は発行時の接続にも使用し、meruto の LAN 非到達時に対応する。
    transport = base[:]
    if args.server:
        transport += ['--server', args.server]
    if args.tls_server_name:
        transport += ['--tls-server-name', args.tls_server_name]
    secret = kubectl_json(transport, ['-n', NAMESPACE, 'get', 'secret', TOKEN_SECRET, '-o', 'json'])
    account = kubectl_json(transport, ['-n', NAMESPACE, 'get', 'serviceaccount', ACCOUNT, '-o', 'json'])
    config = make_config(args.cluster, source['clusters'][0]['cluster'], secret, account,
                         args.server, args.tls_server_name)
    write_config(path, config)
    print(f'Created {path} (0600), context={args.cluster}@muse')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, IndexError, OSError, RuntimeError, subprocess.SubprocessError):
        # 例外本文には秘密値が含まれる可能性があるため表示しない。
        print('Export failed. 接続・RBAC 適用・token 生成・出力先の権限を確認してください。', file=sys.stderr)
        sys.exit(1)

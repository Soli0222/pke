#!/usr/bin/env python3
"""短命・発行回別の証明書で kubectl を1回実行し、専用 RBAC を解除する。

Python の cryptography / PyYAML と kubectl が必要。秘密値は表示しない。
"""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

MANAGER = 'pke-emergency-access'


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context', required=True, help='発行・解除に使う管理 context')
    p.add_argument('--kubeconfig', help='管理 kubeconfig。未指定時も context は必須')
    p.add_argument('--cluster', required=True, choices=['natsume', 'meruto', 'lab'])
    p.add_argument('--person', required=True, help='本人の識別子。認証された個人との対応は利用記録にも残す')
    p.add_argument('--reason', required=True, help='利用理由。秘密値を含めない')
    p.add_argument('--role', choices=['observe', 'cluster-admin'], default='observe')
    p.add_argument('--ttl', type=int, default=3600, help='要求する有効秒数（600〜3600）')
    p.add_argument('--server', help='接続だけを変更する HTTPS endpoint（SSH tunnel 等）')
    p.add_argument('--tls-server-name', help='tunnel 使用時も API の SAN 検証を維持する')
    p.add_argument('command', nargs=argparse.REMAINDER, help='-- に続けて kubectl の引数')
    a = p.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', a.person):
        p.error('person must contain lowercase letters, numbers or hyphens')
    if not 600 <= a.ttl <= 3600:
        p.error('ttl must be between 600 and 3600')
    if not a.reason.strip() or len(a.reason) > 500:
        p.error('reason must be 1..500 characters')
    if a.cluster != 'lab' and a.context not in (a.cluster + '@soli', 'emergency-' + a.cluster + '-legacy-admin'):
        p.error('context does not match the selected production cluster')
    if a.cluster == 'lab' and not a.context.startswith('kind-pke-'):
        p.error('lab requires an explicit kind-pke-* context')
    if a.server and not a.server.startswith('https://'):
        p.error('server must use HTTPS')
    if a.command[:1] == ['--']:
        a.command = a.command[1:]
    if not a.command:
        p.error('provide kubectl arguments after --')
    # 作成した identity 以外で実行する逃げ道を、このラッパーからは渡さない。
    forbidden = ('--kubeconfig', '--context', '--user', '--cluster', '--server',
                 '--tls-server-name', '--insecure-skip-tls-verify', '--token',
                 '--client-key', '--client-certificate', '--certificate-authority', '--as', '--password', '-s')
    if any(arg.split('=', 1)[0].startswith(forbidden) for arg in a.command):
        p.error('connection, credentials and impersonation overrides are not allowed in command')
    return a


class API:
    def __init__(self, args):
        self.owned = []
        self.uncertain = []
        self.base = ['kubectl', '--context', args.context, '--request-timeout=15s']
        if args.kubeconfig:
            self.base += ['--kubeconfig', args.kubeconfig]
        self.transport = []
        if args.server:
            self.transport += ['--server', args.server]
        if args.tls_server_name:
            self.transport += ['--tls-server-name', args.tls_server_name]

    def call(self, args, obj=None):
        r = subprocess.run(self.base + self.transport + args,
                           input=json.dumps(obj) if obj is not None else None,
                           capture_output=True, text=True, timeout=25)
        if r.returncode:
            # API の Status.message にも任意データが含まれるため本文は出力しない。
            raise RuntimeError('kubectl failed: ' + ' '.join(args[:2]))
        return json.loads(r.stdout) if r.stdout.strip() else None

    def create(self, obj):
        # 応答喪失でも作成済みの可能性があるため、同じ発行回のリソースを確認する。
        try:
            result = self.call(['create', '-f', '-', '-o', 'json'], obj)
        except Exception:
            try:
                result = self.call(['get', obj['kind'], obj['metadata']['name'], '--ignore-not-found', '-o', 'json'])
                if result:
                    if result['metadata'].get('annotations', {}).get('pke.str08.net/subject') != obj['metadata']['annotations']['pke.str08.net/subject']:
                        raise RuntimeError('resource ownership mismatch')
                    self.owned.append(result)
            except Exception:
                self.uncertain.append(obj['kind'])
            raise
        self.owned.append(result)
        return result

    def delete_owned(self, obj):
        plural = {'CertificateSigningRequest': 'certificatesigningrequests',
                  'ClusterRole': 'clusterroles', 'ClusterRoleBinding': 'clusterrolebindings'}[obj['kind']]
        path = '/apis/' + obj['apiVersion'] + '/' + plural + '/' + obj['metadata']['name']
        # 同名の他リソースを削除しない。再作成・置換されたら API が拒否する。
        self.call(['delete', '--raw=' + path, '-f', '-'], {
            'apiVersion': 'v1', 'kind': 'DeleteOptions',
            'preconditions': {'uid': obj['metadata']['uid']},
        })


def validate_certificate(pem, key, subject, ttl):
    cert = x509.load_pem_x509_certificate(pem)
    now = dt.datetime.now(dt.timezone.utc)
    if list(cert.subject) != [x509.NameAttribute(NameOID.COMMON_NAME, subject)]:
        raise RuntimeError('unexpected certificate subject/groups')
    if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
        raise RuntimeError('certificate not currently valid')
    if (cert.not_valid_after_utc - now).total_seconds() > ttl + 30:
        raise RuntimeError('signer returned a certificate exceeding requested expiry')
    if cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
        raise RuntimeError('certificate public key mismatch')
    if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise RuntimeError('unexpected CA certificate')
    if set(cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != {ExtendedKeyUsageOID.CLIENT_AUTH}:
        raise RuntimeError('unexpected certificate key usage')
    return cert


def main():
    a = parse_args()
    api = API(a)
    issue = uuid.uuid4().hex
    name = 'pke-breakglass-' + issue
    subject = f'pke:breakglass:{a.cluster}:{a.person}:{issue}'
    owned = api.owned
    cleanup_failed = []
    # 終了シグナルでも finally を通す。SIGKILL / 端末喪失時は期限と手動解除を使う。
    def interrupted(signum, frame):
        raise RuntimeError('session interrupted')
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    try:
        conf = api.call(['config', 'view', '--raw', '--minify', '--flatten', '-o', 'json'])
        cluster = conf['clusters'][0]['cluster']
        if cluster.get('insecure-skip-tls-verify') or not cluster.get('certificate-authority-data'):
            raise RuntimeError('embedded CA and TLS verification are required')
        if not cluster.get('server', '').startswith('https://'):
            raise RuntimeError('HTTPS API endpoint is required')
        if a.cluster == 'lab' and not cluster['server'].startswith('https://127.0.0.1:'):
            raise RuntimeError('lab API must be on loopback')
        # 管理資格情報をコピーせず、接続先と CA だけを取り出す。
        cluster = {k: v for k, v in cluster.items() if k in ('server', 'certificate-authority-data', 'tls-server-name')}
        del conf
        actor = api.call(['auth', 'whoami', '-o', 'json'])['status']['userInfo']['username']
        cluster_uid = api.call(['get', 'namespace', 'kube-system', '-o', 'json'])['metadata']['uid']
        emit(event='start', cluster=a.cluster, cluster_uid=cluster_uid, approver=actor,
             subject=subject, role=a.role, reason=a.reason, cleanup_name=name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        csr = x509.CertificateSigningRequestBuilder().subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])).sign(key, hashes.SHA256())
        metadata = {'name': name, 'labels': {'app.kubernetes.io/managed-by': MANAGER},
                    'annotations': {'pke.str08.net/subject': subject, 'pke.str08.net/reason': a.reason}}
        request = api.create({'apiVersion': 'certificates.k8s.io/v1', 'kind': 'CertificateSigningRequest',
                              'metadata': metadata, 'spec': {
                                  'request': base64.b64encode(csr.public_bytes(serialization.Encoding.PEM)).decode(),
                                  'signerName': 'kubernetes.io/kube-apiserver-client',
                                  'expirationSeconds': a.ttl, 'usages': ['client auth']}})
        api.call(['certificate', 'approve', name, '-o', 'json'])
        for _ in range(30):
            request = api.call(['get', 'csr', name, '-o', 'json'])
            encoded = request.get('status', {}).get('certificate')
            if encoded:
                break
            time.sleep(1)
        else:
            raise RuntimeError('certificate signing timed out')
        cert = validate_certificate(base64.b64decode(encoded), key, subject, a.ttl)
        expiry = cert.not_valid_after_utc.isoformat()
        metadata['annotations']['pke.str08.net/expires-at'] = expiry
        role_name = 'cluster-admin'
        if a.role == 'observe':
            role_name = name
            api.create({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
                                     'metadata': metadata, 'rules': [
                                         {'apiGroups': [''], 'resources': ['nodes'], 'verbs': ['get', 'list']},
                                         {'nonResourceURLs': ['/readyz', '/version'], 'verbs': ['get']},
                                     ]})
        with tempfile.TemporaryDirectory(prefix='pke-emergency-') as directory:
            path = Path(directory) / 'kubeconfig'
            kubeconfig = {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': a.cluster, 'cluster': cluster}],
                          'users': [{'name': subject, 'user': {'client-certificate-data': encoded,
                                     'client-key-data': base64.b64encode(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()}}],
                          'contexts': [{'name': subject, 'context': {'cluster': a.cluster, 'user': subject}}],
                          'current-context': subject}
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
                yaml.safe_dump(kubeconfig, f)
            client = ['kubectl', '--kubeconfig', str(path), '--context', subject] + api.transport
            r = subprocess.run(client + ['--request-timeout=15s', 'auth', 'whoami', '-o', 'json'], capture_output=True, text=True, timeout=25)
            if r.returncode:
                raise RuntimeError('new certificate authentication failed')
            identity = json.loads(r.stdout)['status']['userInfo']
            if identity['username'] != subject or set(identity.get('groups', [])) != {'system:authenticated'}:
                raise RuntimeError('new identity has unexpected name/groups')
            before = subprocess.run(client + ['--request-timeout=15s', 'auth', 'can-i', 'list', 'nodes'], capture_output=True, text=True, timeout=25)
            if before.stdout.strip() != 'no':
                raise RuntimeError('new identity already has node access before the grant')
            api.create({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
                                     'metadata': metadata,
                                     'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': role_name},
                                     'subjects': [{'kind': 'User', 'apiGroup': 'rbac.authorization.k8s.io', 'name': subject}]})
            emit(event='granted', subject=subject, expires_at=expiry, role=a.role)
            # 実行する kubectl の通常出力は利用者が管理する。Secret を出力するコマンドは避ける。
            try:
                result = subprocess.run(client + a.command, timeout=max(1, (cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)).total_seconds()))
            finally:
                # 一時 kubeconfig が存在する間に解除と認可の再確認まで行う。
                while owned:
                    api.delete_owned(owned[-1])
                    owned.pop()
                after = subprocess.run(client + ['--request-timeout=15s', 'auth', 'can-i', 'list', 'nodes'], capture_output=True, text=True, timeout=25)
                if after.stdout.strip() != 'no':
                    raise RuntimeError('node access still allowed or revocation could not be verified')
                emit(event='revoked', subject=subject, node_access='no')
            return result.returncode
    finally:
        # 権限の解除を先に行い、次に role / CSR。CSR 削除は証明書失効ではない。
        for obj in reversed(owned):
            try:
                api.delete_owned(obj)
            except Exception:
                cleanup_failed.append(obj['kind'])
        emit(event='cleanup', name=name, failed=cleanup_failed, uncertain=api.uncertain)
        if cleanup_failed or api.uncertain:
            raise RuntimeError('cleanup incomplete; remove the named resources with the original admin path')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # traceback やレスポンス本文は資格情報を含み得るため、既知の失敗だけを表示。
        print(str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__, file=sys.stderr)
        sys.exit(1)

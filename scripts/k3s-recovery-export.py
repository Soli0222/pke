#!/usr/bin/env python3
"""server の root で実行し、復旧一式を tar stream として stdout に返す。

単体で端末へ実行しない。backup-k3s-recovery.py が暗号化パイプへ接続する。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError('recovery export command failed: ' + Path(args[0]).name)
    return result.stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-host', required=True, choices=['natsume-03', 'meruto-01'])
    a = p.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != a.expected_host:
        raise RuntimeError('host identity or root privilege does not match')
    if sys.stdout.isatty():
        raise RuntimeError('refusing to write recovery secrets to a terminal')
    os.umask(0o077)
    token_path = Path('/var/lib/rancher/k3s/server/token')
    token = token_path.read_bytes()
    if not token.strip():
        raise RuntimeError('empty server token')
    required = ['/etc/etcd/pki', '/etc/rancher/k3s',
                '/etc/systemd/system/etcd.service', '/etc/systemd/system/k3s.service']
    optional = ['/etc/systemd/system/k3s.service.env', '/etc/systemd/system/k3s.service.d',
                '/etc/systemd/system/etcd.service.d']
    if not all(Path(path).exists() for path in required):
        raise RuntimeError('required recovery configuration is missing')
    etcd_version = run(['/usr/local/bin/etcdctl', 'version']).decode().splitlines()[0]
    version_match = re.fullmatch(r'etcdctl version: ([0-9]+\.[0-9]+\.[0-9]+)', etcd_version)
    candidates = [Path('/usr/local/bin/etcdutl')]
    if version_match:
        candidates.append(Path('/var/lib/etcd/downloads') / ('etcd-v' + version_match[1] + '-linux-amd64') / 'etcdutl')
    etcdutl = next((path for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)
    if etcdutl is None:
        raise RuntimeError('matching etcdutl is required; no snapshot was taken')
    if run([str(etcdutl), 'version']).decode().splitlines()[0] != etcd_version.replace('etcdctl', 'etcdutl', 1):
        raise RuntimeError('etcdutl and etcdctl versions must match')
    with tempfile.TemporaryDirectory(prefix='pke-recovery-') as work:
        work = Path(work)
        snapshot = work / 'snapshot.db'
        run(['/usr/local/bin/etcdctl', '--endpoints=https://127.0.0.1:2379',
             '--cacert=/etc/etcd/pki/ca.crt', '--cert=/etc/etcd/pki/healthcheck-client.crt',
             '--key=/etc/etcd/pki/healthcheck-client.key', 'snapshot', 'save', str(snapshot)])
        status = json.loads(run([str(etcdutl), 'snapshot', 'status', str(snapshot), '--write-out=json']))
        if token_path.read_bytes() != token:
            raise RuntimeError('server token changed while taking the snapshot; discard and retry')
        (work / 'server-token').write_bytes(token)
        files = {}
        for source in required + [path for path in optional if Path(path).exists()]:
            target = work / 'host-files' / source.lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            if Path(source).is_dir():
                shutil.copytree(source, target, symlinks=False)
            else:
                shutil.copyfile(source, target)
        for path in work.rglob('*'):
            if path.is_file():
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                files[str(path.relative_to(work))] = {'sha256': digest.hexdigest(), 'bytes': path.stat().st_size}
        manifest = {'format': 1, 'host': a.expected_host,
                    'cluster': 'natsume' if a.expected_host == 'natsume-03' else 'meruto',
                    'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                    'datastore': 'external-etcd', 'snapshot_status': status,
                    'k3s_version': run(['/usr/local/bin/k3s', '--version']).decode().splitlines()[0],
                    'etcd_version': etcd_version,
                    'files': files}
        (work / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
            for path in sorted(work.iterdir()):
                archive.add(path, arcname=path.name)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__, file=sys.stderr)
        sys.exit(1)

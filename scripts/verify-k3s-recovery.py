#!/usr/bin/env python3
"""age 復旧アーカイブを復号し、展開せず全ファイルのサイズ・ハッシュを検証する。

復元試験の代わりにはならない。秘密値と個々のファイルのハッシュは出力しない。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def verify(stream, cluster):
    files = {}
    manifest = None
    seen = set()
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or member.name in seen:
                raise RuntimeError('unsafe or duplicate archive member')
            seen.add(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError('archive must contain only regular files and directories')
            content = archive.extractfile(member)
            if member.name == 'manifest.json':
                if member.size > 4 * 1024 * 1024:
                    raise RuntimeError('manifest too large')
                manifest = json.load(content)
                continue
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: content.read(1024 * 1024), b''):
                digest.update(chunk)
                size += len(chunk)
            files[member.name] = {'sha256': digest.hexdigest(), 'bytes': size}
    # tar の終端より後の age 認証タグも検証させる。
    while stream.read(1024 * 1024):
        pass
    if not manifest or manifest.get('format') != 1 or manifest.get('datastore') != 'external-etcd':
        raise RuntimeError('unsupported or missing recovery manifest')
    host = {'natsume': 'natsume-03', 'meruto': 'meruto-01'}[cluster]
    if manifest.get('cluster') != cluster or manifest.get('host') != host:
        raise RuntimeError('archive does not match the selected cluster')
    if manifest.get('files') != files:
        raise RuntimeError('archive integrity verification failed')
    required = ['server-token', 'snapshot.db', 'host-files/etc/rancher/k3s/config.yaml',
                'host-files/etc/etcd/pki/ca.crt', 'host-files/etc/etcd/pki/ca.key',
                'host-files/etc/etcd/pki/server.crt', 'host-files/etc/etcd/pki/server.key',
                'host-files/etc/systemd/system/k3s.service', 'host-files/etc/systemd/system/etcd.service']
    if any(files.get(path, {}).get('bytes', 0) == 0 for path in required):
        raise RuntimeError('required recovery files are empty or missing')
    return {'cluster': cluster, 'host': host, 'decryption_verified': True,
            'file_integrity_verified': True, 'file_count': len(files),
            'restore_verified': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cluster', required=True, choices=['natsume', 'meruto'])
    p.add_argument('--archive', required=True, type=Path)
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument('--identity', type=Path, help='独立保管先から取り出した age 秘密鍵ファイル（0600）')
    identity.add_argument('--identity-ref', help='age 秘密鍵の op:// 参照。メモリから age の stdin に渡す')
    a = p.parse_args()
    if a.identity and (not a.identity.is_file() or a.identity.stat().st_mode & 0o077 or a.identity.stat().st_uid != os.getuid()):
        p.error('identity must be a private file owned by the current user')
    key = None
    if a.identity_ref:
        if not a.identity_ref.startswith('op://'):
            p.error('identity-ref must be an op:// secret reference')
        result = subprocess.run(['op', 'read', a.identity_ref], capture_output=True, timeout=120)
        if result.returncode or not result.stdout.strip() or len(result.stdout) > 16384:
            raise RuntimeError('could not read age identity from 1Password')
        key = result.stdout
    with subprocess.Popen(['age', '--decrypt', '-i', '-' if key else str(a.identity), str(a.archive)],
                          stdin=subprocess.PIPE if key else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            if key:
                process.stdin.write(key)
                process.stdin.close()
                del key
            result = verify(process.stdout, a.cluster)
            if process.wait(timeout=30):
                raise RuntimeError('age decryption failed')
        finally:
            if process.poll() is None:
                process.kill()
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__, file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""external etcd snapshot / server token / 設定を SSH から age に直接渡す。

端末には暗号文だけを書き出す。復号鍵と暗号文のクラスタ外保管・復号検証は別途行う。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cluster', required=True, choices=['natsume', 'meruto'])
    p.add_argument('--recipient', required=True, help='独立保管済みの age 公開受信者 age1...')
    p.add_argument('--output', required=True, type=Path, help='暗号化アーカイブの新規保存先')
    p.add_argument('--forward-agent', action='store_true', help='この SSH 接続に限り sudo 用の agent 転送を明示的に許可')
    a = p.parse_args()
    if not re.fullmatch(r'age1[0-9a-z]{58}', a.recipient):
        p.error('recipient must be an age X25519 public recipient')
    output = a.output.absolute()
    if output.exists() or output.is_symlink():
        p.error('output must not exist')
    if not output.parent.is_dir() or output.parent.stat().st_mode & 0o077:
        p.error('output parent must be an existing private (0700) directory')
    # 受信者の形式を接続前に検証する。
    probe = subprocess.run(['age', '--encrypt', '-r', a.recipient], input=b'', stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if probe.returncode:
        p.error('age rejected the recipient')
    host = {'natsume': 'natsume-03', 'meruto': 'meruto-01'}[a.cluster]
    source = Path(__file__).with_name('k3s-recovery-export.py').read_bytes()
    ssh = None
    encrypt = None
    tmp = None
    try:
        fd, filename = tempfile.mkstemp(prefix='.recovery-', dir=output.parent)
        tmp = Path(filename)
        with os.fdopen(fd, 'wb') as ciphertext:
            ssh = subprocess.Popen(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                    '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
                                    '-o', 'ForwardAgent=' + ('yes' if a.forward_agent else 'no'),
                                    host, 'sudo', '-n', 'python3', '-', '--expected-host', host],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            encrypt = subprocess.Popen(['age', '--encrypt', '-r', a.recipient], stdin=ssh.stdout,
                                       stdout=ciphertext, stderr=subprocess.PIPE)
            ssh.stdout.close()
            ssh.stdout = None
            ssh.communicate(input=source, timeout=600)
            encrypt.communicate(timeout=60)
            if ssh.returncode or encrypt.returncode:
                raise RuntimeError('backup failed; partial ciphertext discarded (check SSH/sudo and snapshot prerequisites)')
            ciphertext.flush()
            os.fsync(ciphertext.fileno())
        # 既存ファイルを置き換えない。暗号文の作成完了後にだけ最終名を公開する。
        os.link(tmp, output)
        digest = hashlib.sha256()
        with output.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        print(json.dumps({'cluster': a.cluster, 'host': host, 'archive': str(output),
                          'encrypted_bytes': output.stat().st_size, 'sha256': digest.hexdigest(),
                          'decryption_verified': False}))
    finally:
        for process in (ssh, encrypt):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if tmp is not None:
            tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)

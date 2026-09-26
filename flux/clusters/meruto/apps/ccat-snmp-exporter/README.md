# CCAT-1 の監視

meruto の SNMP exporter が CCAT-1 (`192.168.10.254`) を SNMP v2c で60秒ごとに読み取り、Alloy 経由で natsume の Mimir に送る。
`if_mib` と `cisco_device` は chart が指定する exporter イメージの標準モジュールを使う。
インターフェースの64bit通信量、errors / discards、リンク状態、CPU・メモリ、SNMP uptimeを収集する。機器が実装していないOIDは取得できない。
`Gi1/0/1` はIX2215側、`Gi1/0/8` はMac側。SNMPの `ifIndex` はポート番号から推測せず、`ifName` / `ifDescr` で識別する。

## Community の登録

ログインパスワードは使わないが、SNMP v2cにはcommunity文字列が必要。
1Password の vault `Kubernetes` に item `ccat-snmp-exporter-config` を作り、`auth.yaml` フィールドに以下を登録する。
`<COMMUNITY>` はスイッチと共通の値に置き換える。実際の値をGitに保存しない。

```yaml
auths:
  ccat_v2:
    version: 2
    community: "<COMMUNITY>"
```

OnePassword Operator が namespace `ccat-snmp-exporter` の Secret `snmp-exporter-config` に同期する。
Secretがない間はexporter Podを起動できない。community更新時はSecret同期後にDeploymentを再起動する。
標準モジュールと認証ファイルは別々の `--config.file` で読み込む。

## スイッチの設定

SNMPの問い合わせ元はmerutoノードの `192.168.10.3` を許可する。PodからLANへの通信はノードでmasqueradeされる前提。
ACL 90が未使用であることを確認してから設定する。SNMP trapの送信設定は不要。

```text
configure terminal
access-list 90 permit host 192.168.10.3
snmp-server community <COMMUNITY> RO 90
logging on
logging buffered 32768 informational
logging trap informational
logging source-interface Vlan1
logging host 192.168.10.4 transport udp port 1514
end
show snmp
show logging
copy running-config startup-config
```

Vectorは送信元 `192.168.10.254` に `host="ccat-1"`、`192.168.10.1` に `host="ix2215-1"` を付ける。
その他の送信元は `host="unknown"` とし、実際の送信元と本文はJSONに保持する。
Lokiの時刻はVectorの受信時刻。syslog本文に含まれる機器時刻は書き換えない。

CCAT-1の既存デフォルトルートが `192.168.1.1` の場合、同一サブネットの収集先には影響しないが、外部NTPへの経路は別途確認する。LAN側のIXアドレスは `192.168.10.1`。

## 反映と確認

Gitの変更をFluxに反映し、`meruto@soli` の `ccat-snmp-exporter` と `vector` のKustomizationについてReadyだけでなく適用revision / generationを確認する。
このアプリの追加はmerutoのみ。natsumeの収集設定は変更しない。

```sh
kubectl --context meruto@soli -n flux-system get kustomization ccat-snmp-exporter vector
kubectl --context meruto@soli -n ccat-snmp-exporter get helmrelease,pod,servicemonitor
```

Mimirでは `up{target="ccat-1",cluster="meruto"}` が1で、以下の実データが届くことを確認する。

```promql
rate(ifHCInOctets{target="ccat-1",cluster="meruto"}[5m]) * 8
increase(ifInErrors{target="ccat-1",cluster="meruto"}[5m])
increase(ifOutDiscards{target="ccat-1",cluster="meruto"}[5m])
ifOperStatus{target="ccat-1",cluster="meruto"}
cpmCPUTotal1minRev{target="ccat-1",cluster="meruto"}
ciscoMemoryPoolUsed{target="ccat-1",cluster="meruto"}
```

Lokiは `{cluster="meruto",source="syslog",host="ccat-1"}` で通常の機器ログを確認する。障害注入やテスト通知は行わない。
Vector更新後はIXのログも継続して届くことを確認する。
UDP syslogが途絶えた場合、送信がないだけなのか、Vectorの受信・転送経路で失われているのかを分ける。
Serviceの転送先Podが変わった際は、ノードのconntrackに古いUDP転送先が残っていないか確認する。
ログがないことだけで機器が正常とは判定しない。

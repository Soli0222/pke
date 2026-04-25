# kkg → VPS リプレース 最終まとめ

## VPS スペック

| 項目 | 選定 |
|------|------|
| プラン | **Kagoya 8コア/16GB（¥7,810/月）** |
| ノード数 | **1台（k3s シングルノード）** |
| ストレージ | 1TB SSD 付属（NAS が実データ担当） |

---

## 構成変更ポイント

| 変更 | 内容 |
|------|------|
| k8s → k3s | 管理プレーン大幅軽量化、Cilium → flannel |
| Grafana Cloud 移行 | mimir / loki / grafana を外出し（無料枠内） |
| CNPG replica 3→1 | 全クラスタ（misskey-dev / mf / spn / reblend / sui） |
| Falco 除去 | — |
| navidrome・Misskey 本番 追加 | natsume-01 を廃止できる |
| navidrome / peertube | navidrome は載せる、peertube は載せない |

---

## メモリ試算

| 内訳 | メモリ |
|------|--------|
| 既存ワークロード | ~4.6GB |
| Misskey 本番 | ~4.0GB |
| navidrome | ~0.05GB |
| k3s + OS | ~1.5GB |
| Grafana Cloud 移行・falco 除去・replica 削減 | -1.3GB |
| **合計** | **~8.85GB** |
| **空き** | **~7.15GB（44%）** |

### クリティカルなメモリ使用

| ワークロード | メモリ | 備考 |
|------------|--------|------|
| Misskey 本番スタック | ~4.0GB | 全体の45%。PGroonga インデックスが支配的 |
| alloy | ~1.2GB → ~200MB | Grafana Cloud 移行で転送専用になれば大幅減 |
| ArgoCD | ~814MB | CD 基盤。削れない |
| komga | ~645MB | JVM 系で膨らみやすい |
| CNPG clusters 合計 | ~400MB | replica=1 後の5クラスタ計 |

---

## コスト

| 項目 | 月額 |
|------|------|
| Kagoya 16GB VPS | ¥7,810 |
| Grafana Cloud | ¥0（無料枠内） |
| **合計** | **¥7,810/月** |

natsume-01 の VPS 代が不要になる分、実質的な差し引きコストはさらに下がる。

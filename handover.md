# handover

## 2026-05-30 natsume ストレージ / ノード追加方針

### 現状

- `natsume-03` は 8 core / 16 GB / NVMe 1000 GB の VPS。
- Misskey CNPG は Longhorn から TopoLVM へ移行済み。
- Misskey CNPG を TopoLVM に移したあと、スロークエリは大きく改善した。
  - 現在のスロークエリはおおむね 1 時間に数件程度。
  - 以前問題になっていた Longhorn 経由のレイテンシ増幅は、かなり解消したと見てよい。
- 一方で、node exporter の disk read はまだ大きく見える。
  - `vda` は root LV / Longhorn LV / TopoLVM LV が混ざるため、単独では判断材料として弱い。
  - 直近の観測では、TopoLVM の `dm-2` だけでなく root LV の `dm-0` からも大きな read が出ていた。
  - ディスクレイテンシは低く、TopoLVM 自体が詰まっているようには見えなかった。
- 残っている問題は、ストレージ単体の問題というよりメモリ / ページキャッシュ不足に近い。
  - Misskey DB、Mimir、Loki、Grafana、Alloy、Longhorn、各種アプリが同じノードに同居している。
  - 複数ワークロードで major page fault が見えていた。

### 前提

当面は単一障害点を許容する。目的は HA ではなく、`natsume-03` 上のリソース干渉を減らすこと。

### VPS 候補

| プラン | スペック | 価格 |
| --- | --- | ---: |
| 小 | 3c / 3 GB / NVMe 400 GB | 1,430 円 |
| 中 | 4c / 4 GB / NVMe 600 GB | 1,760 円 |
| 大 | 6c / 8 GB / NVMe 800 GB | 3,410 円 |
| 現行同等 | 8c / 16 GB / NVMe 1000 GB | 7,810 円 |

### 推奨方針

6c / 8 GB / NVMe 800 GB のノードを 1 台追加する。

新ノードには Misskey DB を移すのではなく、監視系や周辺ワークロードを逃がす。`natsume-03` は Misskey DB 中心のノードとして使う。

推奨配置:

- `natsume-03`
  - Misskey CNPG primary on TopoLVM
  - 可能なら Misskey web / Valkey も同居
  - DB / Misskey 寄りのノードとして扱う
- 新しい 6c / 8 GB ノード
  - Mimir
  - Loki
  - Grafana
  - Alloy
  - Longhorn の残り小さめの volume
  - その他 stateless または低リスクなアプリ

理由:

- Misskey DB を 16 GB の `natsume-03` から 8 GB ノードへ移すと、DB が使えるページキャッシュの上限が下がる。
- 今の症状は、DBだけが重いというより、同一ノード上のワークロードがメモリ / ページキャッシュを食い合っている形に近い。
- そのため、DBを小さいノードへ動かすより、`natsume-03` から監視系・周辺系を逃がすほうが効果が出やすい。
- 3 GB / 4 GB ノードは、Mimir / Loki / Grafana / Alloy を逃がす先としても小さすぎる。
- 8c / 16 GB を追加できるならきれいだが、価格差が大きい。まずは 6c / 8 GB で分離するのが費用対効果がよい。

### ストレージ方針

単一障害点を許容する前提では、次の方針にする。

- 重い stateful workload は TopoLVM / local storage に寄せる。
- 性能が重要な DB では、Longhorn の cross-node replication に依存しない。
- Longhorn は、小さめ・低レイテンシ要求でない volume に限定して使う。
- バックアップ / リカバリは CNPG Barman / R2 / アプリごとの backup に寄せる。

### 避けること

- 最初の一手として Misskey CNPG を 6c / 8 GB ノードへ移さない。
- 3 GB / 4 GB ノードを監視系の移動先として使わない。
- この性能問題を解決するために Longhorn replica 数を増やさない。
- `node_disk_*{device="vda"}` だけで判断しない。
  - `dm-0`
  - `dm-1`
  - `dm-2`
  - container-level I/O
  - disk latency / I/O busy
  を分けて見る。

### 次の実装ステップ

1. 新ノードを Ansible inventory / host_vars に追加する。
2. 新ノードを `natsume` K3s cluster に join する。
3. stateful workload を置く場合は、新ノードにも TopoLVM 用ストレージを用意する。
4. 監視系 / 周辺系ワークロード用の node label を付ける。
5. Flux 側で Mimir、Loki、Grafana、Alloy、その他 Misskey 以外のワークロードに affinity / nodeSelector を追加する。
6. 移動後に以下を再確認する。
   - `natsume-03` の `node_memory_MemAvailable_bytes`
   - major page fault rate
   - `dm-2` の read latency と I/O busy
   - Misskey slow query の頻度
   - root LV `dm-0` の read volume

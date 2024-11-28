# Polestar Kubernetes Engine

Soliが開発・運用・保守を行っているサービス群のオンプレ環境用リポジトリ

クラスタの命名規則は以下の規則に則ったものとする。

```
${クラスタ名}-${仮想化基盤名}-${番号}
```

運用中のクラスタは以下の通りである

### okyクラスタ

物理ノード3台によって構成されるクラスタ  
仮想化基盤はProxmoxを採用 

- oky-pve-1 192.168.20.2
- oky-pve-2 192.168.20.3
- oky-pve-3 192.168.20.3

## Terraform

VMの作成はTerraformを用いて行う  
命名規則は以下の通りである

### Kubernetes向けVM

```
pke-${クラスタ名}-${ノードロール}${番号}
```

### oky

okyクラスタ向けには以下のVMが作成される  
lbノードはHAProxy用

| VM名          | CPU コア数  | メモリ   | ディスク容量   | IPアドレス       | ホストマシン   | 
| ------------- | ---------- | ------- | ------------ | --------------- | ------------ | 
| pke-oky-lb-1  | 4          | 4GB     | 50GB         | 192.168.20.31   | oky-pve-1    | 
| pke-oky-lb-2  | 4          | 4GB     | 50GB         | 192.168.20.32   | oky-pve-2    |
| pke-oky-cp-1  | 4          | 4GB     | 50GB         | 192.168.20.51   | oky-pve-1    | 
| pke-oky-cp-2  | 4          | 4GB     | 50GB         | 192.168.20.52   | oky-pve-2    |  
| pke-oky-cp-3  | 4          | 4GB     | 50GB         | 192.168.20.53   | oky-pve-3    |  
| pke-oky-wk-1  | 4          | 6GB     | 50GB         | 192.168.20.101  | oky-pve-1    | 
| pke-oky-wk-2  | 4          | 6GB     | 50GB         | 192.168.20.102  | oky-pve-2    |
| pke-oky-wk-3  | 4          | 10GB    | 50GB         | 192.168.20.103  | oky-pve-3    | 

![構成図](https://media.soli0222.com/polestar/d356e077-effe-416f-bc35-fec01c91cf0c.png)

#### 今後

今後は1ノードあたり2VMを上限とし(メモリ16GBの場合)、合計4ノードでの構成を検討。

![構成図_計画](https://media.soli0222.com/polestar/6ee80145-3491-4748-ae84-ce8d51c20b48.png)
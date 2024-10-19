# Polestar Kubernetes Engine

Soliが開発・運用・保守を行っているサービス群のオンプレ環境用リポジトリ

クラスタの命名規則は以下の規則に則ったものとする。

```
${クラスタ名}-${仮想化基盤名}-${番号}
```

運用中のクラスタは以下の通りである

### okyクラスタ

N100 2台によって構成されるクラスタ  
仮想化基盤はProxmoxを採用 

- oky-pve-1 192.168.20.2
- oky-pve-2 192.168.20.3

## Terraform

VMの作成はTerraformを用いて行う  
命名規則は以下の通りである

### Kubernetes向けVM

```
${pke}-${クラスタ名}-${ノードロール}${番号}
```

### oky

okyクラスタ向けには以下のVMが作成される

| VM名        | CPU コア数 | メモリ | ディスク容量 | IPアドレス    | ホストマシン | 
| ----------- | ---------- | ------ | ------------ | ------------- | ------------ | 
| pke-oky-m1  | 4          | 4GB    | 50GB         | 192.168.20.11 | oky-pve-1    | 
| pke-oky-m2  | 4          | 4GB    | 50GB         | 192.168.20.12 | oky-pve-2    | 
| pke-oky-w1  | 4          | 4GB    | 50GB         | 192.168.20.13 | oky-pve-1    | 
| pke-oky-w2  | 4          | 4GB    | 50GB         | 192.168.20.14 | oky-pve-1    | 
| pke-oky-w3  | 4          | 4GB    | 50GB         | 192.168.20.15 | oky-pve-2    | 
| pke-oky-w4  | 4          | 4GB    | 50GB         | 192.168.20.16 | oky-pve-2    | 
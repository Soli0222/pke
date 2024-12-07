### 手順
1. Gitリポジトリをクローンします。
   ```sh
   git clone https://github.com/SynologyOpenSource/synology-csi.git
   ```

2. ディレクトリに移動します。
   ```sh
   cd synology-csi
   ```

3. `client-info-template.yml` ファイルをコピーします。
   ```sh
   cp config/client-info-template.yml config/client-info.yml
   ```

4. `config/client-info.yml` を編集して、DSMの接続情報を設定します。CSIボリュームが作成されるストレージシステムを**1つ以上**指定できます。以下のパラメータを必要に応じて変更してください：
    - **host**: DSMのIPv4アドレス。
    - **port**: DSMに接続するためのポート。デフォルトのHTTPポートは5000、HTTPSポートは5001です。異なるポートを使用する場合は変更してください。
    - **https**: HTTPSを使用する場合は "true" に設定します。この場合、ポートも適切に設定してください。
    - **username**, **password**: DSMに接続するための認証情報。

5. インストール
    * **YAML**
        ドライバをインストールするには、以下のコマンドを実行します。
        - *ベーシック*:
            ```sh
            ./scripts/deploy.sh build && ./scripts/deploy.sh install --basic
            ```

        Bashスクリプトを実行すると、以下の操作が行われます：
        - "`synology-csi`" という名前のネームスペースが作成されます。ここにドライバがインストールされます。
        - `client-info.yml` で設定した認証情報を使用して、"`client-info-secret`" という名前のシークレットが作成されます。
        - ローカルイメージをビルドし、CSIドライバをデプロイします。
        - "`Retain`" ポリシーを使用する "`synology-iscsi-storage`" という名前の**デフォルト**ストレージクラスが作成されます。

    * **HELM** (ローカル開発)
        1. ネームスペースを作成します。
           ```sh
           kubectl create ns synology-csi
           ```
        2. シークレットを作成します。
           ```sh
           kubectl create secret -n synology-csi generic client-info-secret --from-file=./config/client-info.yml
           ```
        3. Helmチャートをデプロイします。
           ```sh
           cd deploy/helm; make up
           ```

6. CSIドライバのすべてのポッドのステータスがRunningであることを確認します。
   ```sh
   kubectl get pods -n synology-csi
   ```

## CSIドライバの設定
ストレージクラスとシークレットは、CSIドライバが正常に動作するために必要です。このセクションでは、以下の手順を説明します：
1. ストレージシステムのシークレットを作成する（通常、`deploy.sh`がすべての設定を完了します）。
2. ストレージクラスを設定する。
3. ボリュームスナップショットクラスを設定する。

### シークレットの作成
ストレージシステムのアドレスと認証情報（ユーザー名とパスワード）を指定するシークレットを作成します。通常、設定ファイルがシークレットを設定しますが、シークレットを手動で作成または再作成する場合は、以下の手順に従ってください：

1. 設定ファイル `config/client-info.yml` を編集するか、以下の例のように新しいファイルを作成します：
    ```yaml
    clients:
    - host: 192.168.1.1
      port: 5000
      https: false
      username: <username>
      password: <password>
    - host: 192.168.1.2
      port: 5001
      https: true
      username: <username>
      password: <password>
    ```
    `clients` フィールドには複数のSynology NASを含めることができます。各エントリを `-` で区切ります。

2. 以下のコマンドを使用してシークレットを作成します（通常は `deploy.sh` が実行します）：
    ```sh
    kubectl create secret -n <namespace> generic client-info-secret --from-file=config/client-info.yml
    ```

    - `<namespace>` を `synology-csi` に置き換えます。これはデフォルトのネームスペースです。必要に応じてカスタムネームスペースに変更してください。
    - シークレット名 "client-info-secret" を変更する場合は、`deploy/kubernetes/<k8s version>/` 内のすべてのファイルが設定したシークレット名を使用していることを確認してください。

### ストレージクラスの作成
希望するプロパティを持つストレージクラスを作成して適用します。

```sh
kubectl apply -f oky-synology-01.yaml
```
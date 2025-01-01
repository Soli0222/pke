# Spotify Now Playing

Spotify Now Playingは、Spotifyの現在再生中の曲情報を表示するKubernetesアプリケーションです。このHelmチャートを使用して、簡単にデプロイできます。

## インストール

1. Helmチャートをインストールします。

    ```bash
    helm install spotify-nowplaying . 
    ```

## 設定



values.yaml

 ファイルで設定をカスタマイズできます。主な設定項目は以下の通りです。

```yaml
image:
  tag: 2.2.0

env:
  PORT: 8080
  SERVER_URI: mi.soli0222.com
  SPOTIFY_REDIRECT_URI_NOTE: https://spn.soli0222.com/note/callback
  SPOTIFY_REDIRECT_URI_TWEET: https://spn.soli0222.com/tweet/callback
```

必要に応じて、`values.yaml` を編集してからインストールを行ってください。

## シークレットの作成

SpotifyのクライアントIDとクライアントシークレットを設定するために、Kubernetesシークレットを作成する必要があります。以下の手順で作成してください。

1. クライアントIDとシークレットをBase64エンコードします。

    ```bash
    echo -n 'your_spotify_client_id' | base64
    echo -n 'your_spotify_client_secret' | base64
    ```

2. `secret.yaml` ファイルを作成または更新します。

    ```yaml
    apiVersion: v1
    kind: Secret
    metadata:
      name: spotify-nowplaying-secret
    type: Opaque
    data:
      SPOTIFY_CLIENT_ID: <base64_encoded_client_id>
      SPOTIFY_CLIENT_SECRET: <base64_encoded_client_secret>
    ```

3. シークレットを適用します。

    ```bash
    kubectl apply -f secret.yaml
    ```

## アンインストール

Helmリリースを削除するには、以下のコマンドを実行します。

```bash
helm uninstall spotify-nowplaying
```

## アップデート

チャートやアプリケーションのバージョンを更新する場合、以下のコマンドを使用します。

```bash
helm upgrade spotify-nowplaying . -f values.yaml
```

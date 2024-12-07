# Cloudflared Deployment Guide

このガイドでは、Cloudflared を Kubernetes クラスターにデプロイする手順を説明します。

## 手順

1. **ネームスペースの作成**

   Cloudflared 用のネームスペースを作成します。

   ```sh
   kubectl create namespace cloudflared
   ```

2. **Cloudflared トンネルのログイン**

   Cloudflared トンネルにログインします。

   ```sh
   cloudflared tunnel login
   ```

3. **Cloudflared トンネルの作成**

   トンネルを作成します。`${TUNNEL_NAME}` を適切なトンネル名に置き換えてください。

   ```sh
   cloudflared tunnel create ${TUNNEL_NAME}
   ```

4. **トンネル認証情報の Kubernetes シークレット作成**

   トンネルの認証情報を Kubernetes シークレットとして作成します。`${CREDENTIALS_PATH}` を認証情報ファイルのパスに置き換えてください。

   ```sh
   kubectl -n cloudflared create secret generic tunnel-credentials --from-file=credentials.json=${CREDENTIALS_PATH}
   ```

5. **Helm を使用して Cloudflared をインストール**

   `values.${TUNNEL_NAME}.yaml` ファイルを使用して、Helm で Cloudflared をインストールします。

   ```sh
   helm install -n cloudflared cloudflared . -f values.${TUNNEL_NAME}.yaml
   ```

これで、Cloudflared が Kubernetes クラスターにデプロイされます。
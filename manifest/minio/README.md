# MinIO Operator Deployment Guide

このガイドでは、MinIO Operator を Kubernetes クラスターにデプロイする手順を説明します。

## 手順

1. **Helm リポジトリの追加**

   MinIO Operator の Helm リポジトリを追加します。

   ```sh
   helm repo add minio-operator https://operator.min.io
   ```

2. **MinIO Operator のインストール**
   
   MinIO Operator をインストールします。

   ```sh
   helm install \
   --namespace minio-operator \
   --create-namespace \
   operator minio-operator/operator
   ```

3. **MinIO Tenant のインストール**
   
   ```sh
   helm install -n minio-tenant minio-tenant . -f values.yaml
   ```


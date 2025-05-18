# MinIO Operator Deployment Guide

このガイドでは、MinIO Operator を Kubernetes クラスターにデプロイする手順を説明します。

## 手順

1. **Helm リポジトリの追加**

   MinIO Operator の Helm リポジトリを追加します。

   ```sh
   helm repo add minio https://operator.min.io
   ```

2. **MinIO Operator のインストール**
   
   MinIO Operator をインストールします。

   ```sh
   helm install \
   --namespace minio-operator \
   --create-namespace \
   operator minio/operator
   ```

3. **MinIO Tenant のインストール**
   
   ```sh
   kubectl apply -f onepassworditem.yaml -n minio-tenant
   helm upgrade --install -n minio-tenant tenant minio/tenant -f values.yaml
   ```


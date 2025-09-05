# Bunny CDN Terraform Configuration

この Terraform 設定は、bunny.net CDN サービスのプルゾーンを管理するためのものです。

## 構成要素

- **Pull Zone**: CDN 配信のためのプルゾーン（キャッシュとコンテンツ配信）

## 使用方法

1. 環境の準備:
   ```bash
   source setup.sh
   ```

2. Terraform の初期化:
   ```bash
   terraform init
   ```

3. 実行計画の確認:
   ```bash
   terraform plan
   ```

4. リソースの作成:
   ```bash
   terraform apply
   ```

## 環境変数

`setup.sh` で設定される環境変数:
- `TF_VAR_bunny_api_key`: Bunny.net API キー（1Password から取得）

## 機能

### プルゾーン（CDN）
- 標準ティアでのルーティング
- キャッシュ制御の最適化
- 圧縮の有効化
- スマートキャッシュの有効化
- 地理的ゾーン（アジア、ヨーロッパ、米国）の有効化
- AVIF Vary サポート
- クエリ文字列の並び替え

## セキュリティ設定

- SSL 証明書の検証
- キャッシュエラーレスポンス
- 適切なキャッシュ制御ヘッダーの設定

## カスタマイズ

必要に応じて `main.tf` の設定を変更できます：
- オリジンURLの変更
- キャッシュ時間の調整
- セキュリティ設定の変更
- 追加の地理的制限
- レート制限の設定

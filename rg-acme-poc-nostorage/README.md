# ACME(Let's Encrypt) PoC — Storage不使用版(rg-acme-poc-nostorage)

`rg-acme-poc`(Blob版、`../README.md`)と全く同じ検証を、**Storageアカウントの匿名blobアクセス
(`allowBlobPublicAccess`)に一切依存せずに行う版**。本番環境で「Storage匿名blobアクセスを禁止する」
Azure Policyが既に適用されている場合、Blob版のパターン(チャレンジ応答を`$web`静的Webサイトで
匿名公開する)はそのまま持ち込めない(ポリシー例外申請が必須になる)。この版はその例外申請を
不要にするための構成。

**リソースグループを完全に分離している**(`rg-acme-poc-nostorage`)。Blob版のrg(`rg-acme-poc`)には
一切手を入れない。ドメインも衝突しないよう`acme-test2.covesun.net`を使う(Blob版は`acme-test.covesun.net`)。

## Blob版との違い(ここだけ読めば十分)

| 項目 | Blob版(`rg-acme-poc`) | この版(`rg-acme-poc-nostorage`) |
|---|---|---|
| チャレンジ応答の配信元 | Storage `$web`静的Webサイト(匿名blobアクセス) | Functions on ACAコンテナ自身のHTTPルート(`/.well-known/acme-challenge/{token}`) |
| Storageアカウント | `allowBlobPublicAccess: true`(静的サイト用) | `allowBlobPublicAccess: false`(Functionsランタイム用のみ。チャレンジ配信には使わない) |
| auth-hook.sh / cleanup-hook.sh | `az storage blob upload/delete`(要`az login --identity`) | ローカルディスク(`/tmp/acme-challenge/`)への読み書きのみ。Azure API呼び出しなし |
| deploy-hook.sh | 変更なし(Key Vault import + 通知) | 変更なし |
| Function Appのスケール | 制約なし | `minReplicas`/`maxReplicas`を1に固定必須(hookとHTTPルートで同一ファイルシステムを共有する必要があるため) |
| Storage Blob Data Contributorロール | チャレンジblob操作に必要 | Functionsランタイムの裏側ストレージ用として残しているが、チャレンジ配信では未使用 |
| ドメイン | `acme-test.covesun.net` | `acme-test2.covesun.net` |
| AGWの`challengeBackendFqdn` | Storage静的サイトのホスト名 | Function Appのdefault hostname(内部FQDN) |

デプロイ手順・トラブルシュートの大部分(AGWの2段階デプロイパターン、healthzプローブ、
`az login --identity`が必要な理由、App Insightsがworkspaceベースな点、`az functionapp stop/start`での
手動テスト、Key Vault確認コマンドなど)は`../README.md`とまったく同じなので、そちらを参照。
以下はこの版特有の手順だけ書く。

## デプロイ手順(この版特有の差分)

### 0. 事前準備
```
az group create -n rg-acme-poc-nostorage -l japaneast
```
Cloudflareの`acme-test2.covesun.net`のAレコードは、フェーズ1で払い出されるPublic IPが確定してから設定。

### 1. フェーズ1: 基盤リソースをデプロイ
```
az deployment group create \
  -g rg-acme-poc-nostorage \
  -f 01-foundation.json \
  -p 01-foundation.parameters.json
```
`storageAccountName`(`stacmefuncnstv43jy`)は既に具体的な値を入れてあるが、
`containerRegistryName`はまだプレースホルダーのままなので、グローバルに一意な実際の値に
置き換えてから実行すること。

Storageの静的Webサイト有効化・healthzブロブ配置(Blob版のステップ2)は**この版では不要**。

### 2. フェーズ2a: AGWを80番リスナーのみでブートストラップ

Blob版と同じ手順だが、`challengeBackendFqdn`にはまだ値を入れられない
(Function Appを先に作る必要があるため、この時点では後回しにしてよい —
Function Appのdefault hostnameは`az functionapp create`直後から決まっているので、
先にFunction Appだけ作ってhostnameを取得してからこのデプロイをしても、逆の順番でも構わない)。

```
az deployment group create \
  -g rg-acme-poc-nostorage \
  -f 02a-appgateway-bootstrap.json \
  -p 02a-appgateway-bootstrap.parameters.json
```

### 3. Functions on ACA(certbot)のビルド・デプロイ

`functions-on-aca/README.md`(この版専用)を参照。Blob版と同じ2段階作成パターン
(公開プレースホルダーイメージ→本物のACRイメージに切り替え)に加えて、
**`minReplicas`/`maxReplicas`を1に固定する手順が追加**である点に注意。

Function App作成後:
```
az functionapp show -g rg-acme-poc-nostorage -n func-acme-poc-li0cfu --query defaultHostName -o tsv
```
で取得したFQDNを`02a-appgateway-bootstrap.parameters.json`/`02-appgateway.parameters.json`の
`challengeBackendFqdn`に設定し、まだ未デプロイならステップ2のデプロイを実行(既にデプロイ済みなら
再デプロイして`challengeBackendFqdn`を反映)。

バックエンド健全性の確認は`az network application-gateway show-backend-health`で。
`Unhealthy`ならFunction Appの`healthz`ルートが`auth_level=ANONYMOUS`になっているか、
`minReplicas`が0になっていないか(0だとコールドスタートでプローブがタイムアウトする可能性)を疑う。

### 4. フェーズ2b: AGWに443リスナーを追加

Blob版と同じ。`02-appgateway.parameters.json`のプレースホルダーを埋めてデプロイ。

## 要検証・要確認(この版特有)

- AGWのバックエンドHTTP設定(HTTPS・443番・`pickHostNameFromBackendAddress`)がFunctions on ACAの
  default hostname(ACAのマネージド証明書)に対してそのまま疎通するか、実機未検証。
- `az containerapp update --min-replicas 1 --max-replicas 1`がFunctions on ACAに正しく効くか未検証。
- Blob版で確認済みの「staging証明書でのE2E成功」は、この版ではまだ実機検証していない
  (Blob版で確立した設計をそのまま踏襲しているが、チャレンジ配信経路が変わっているため個別に検証要)。
- Storageアカウントの`Storage Blob Data Contributor`ロールが本当に不要かどうか(Functionsランタイムが
  裏側で使う分の権限として最低限必要な可能性があり、念のため残してある)。

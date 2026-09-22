# Functions on ACA — certbotコンテナ

## 必要な環境変数(Function App の設定に追加)

| 変数名 | 用途 | 例 |
|---|---|---|
| `ACME_DOMAIN` | 発行対象ドメイン | `acme-test.covesun.net` |
| `ACME_EMAIL` | ACMEアカウントのメール | (Pのメールアドレス) |
| `ACME_SERVER` | ACMEディレクトリURL | `https://acme-staging-v02.api.letsencrypt.org/directory`(最初はstaging) |
| `CHALLENGE_STORAGE_ACCOUNT` | チャレンジ応答用Storageアカウント名 | `stacmechald0emijex` |
| `KEYVAULT_NAME` | 証明書インポート先Key Vault | `kv-acme-poc` |
| `KEYVAULT_CERT_NAME` | Key Vault内の証明書オブジェクト名 | `acme-test-covesun-net` |
| `ACS_ENDPOINT` | Azure Communication ServicesのEmail用エンドポイント | `https://<acs-resource>.communication.azure.com` |
| `ACS_SENDER_ADDRESS` | ACSの検証済み送信元アドレス | `DoNotReply@xxxx.azurecomm.net` |
| `NOTIFY_TO_ADDRESS` | 通知先メールアドレス | (Pのメールアドレス) |
| `AZURE_CLIENT_ID` | `id-func-poc`のクライアントID(ユーザー割り当てIDをDefaultAzureCredentialに明示させるため) | `01-foundation.json`のデプロイ後に`az identity show`で取得 |
| `CERTBOT_SCHEDULE` | Timerトリガーのスケジュール(NCRONTAB)。`function_app.py`が`%CERTBOT_SCHEDULE%`で参照しており、未設定だと実行時エラーになる | `0 0 3 * * *`(毎日AM3時UTC) |
| `CERTBOT_RUN_ON_STARTUP` | `true`でコンテナ起動時に即時実行(手動テスト用)。既定は`false`扱い | `true`/`false` |

## 手動実行の注意(ACA環境がinternal限定のため)

ACA環境は`internal: true`(VNet内限定)。Function Appのdefault hostnameもVNet内部のプライベートDNSでしか
名前解決できないため、**Azure Portalの「コード+テスト」もVNet外からのcurlも使えない**(名前解決の時点で失敗する)。

Timerトリガーの発火自体はACAのスケールコントローラーが内部的に行うものでingressと無関係なので、
`CERTBOT_RUN_ON_STARTUP=true`を設定して`az functionapp restart`することで、外部からの到達性なしに
即時実行できる。詳細コマンドは`../README.md`のステップ5を参照。

## ビルド & プッシュ

ACRは`01-foundation.json`で作成済み(`acracmepoc6mgbgs`、admin無効・RBACのみ)。ログインしてビルド&プッシュするだけでいい:
```
cd functions-on-aca
az acr login -n acracmepoc6mgbgs
docker build -t acracmepoc6mgbgs.azurecr.io/acme-certbot:latest .
docker push acracmepoc6mgbgs.azurecr.io/acme-certbot:latest
```

## Function App 作成(Functions on ACA) — マネージドID経由でACR pull、admin認証情報は一切使わない

**ACRの`adminUserEnabled`はテンプレート通り`false`のまま。以下の手順ならadmin認証情報は不要。**

`az functionapp create`に最初から`<ACR名>.azurecr.io/acme-certbot:latest`のような**プライベートイメージを直接指定すると、
admin認証情報の自動lookupが走って失敗する**(マネージドIDが未割当の状態でCLIがこの経路に入ってしまう)。
また`--image`を省略して作成しようとすると`Cannot find FunctionApp with name ...`で作成自体が失敗する
(公式ドキュメントは`--image`省略を案内しているが、現行CLIではこの通りに動かなかった)。

そのため、**公開のプレースホルダーイメージでいったん作成 → 後からACRの本物のイメージ+マネージドID認証に切り替える**、
という2段階の手順を取る:

```
# 1. Function App作成(公開のプレースホルダーイメージ、認証不要)
FUNC_IDENTITY_ID=$(az identity show -g rg-acme-poc -n id-func-poc --query id -o tsv)

az functionapp create \
  -g rg-acme-poc \
  -n func-acme-poc \
  --environment env-acme-poc \
  --storage-account <storageAccountName> \
  --functions-version 4 \
  --image mcr.microsoft.com/azure-functions/dotnet8-quickstart-demo:1.0 \
  --assign-identity "$FUNC_IDENTITY_ID"

# 2. 本物のイメージ+マネージドID認証への切り替え設定をファイルで用意
#    (シェルへのインラインJSON展開はエスケープが崩れやすいので必ずファイル経由にする)
#    DOCKER_REGISTRY_SERVER_URL は https:// を付けない、ホスト名だけ
#    (スキーム付きだと ContainerAppInvalidRegistryServerValue で拒否される)
cat > /tmp/patch.json << 'EOF'
{
  "siteConfig": {
    "linuxFxVersion": "DOCKER|<ACR名>.azurecr.io/acme-certbot:latest",
    "acrUseManagedIdentityCreds": true,
    "acrUserManagedIdentityID": "REPLACE_WITH_IDENTITY_ID",
    "appSettings": [
      { "name": "DOCKER_REGISTRY_SERVER_URL", "value": "<ACR名>.azurecr.io" }
    ]
  }
}
EOF
sed -i '' "s|REPLACE_WITH_IDENTITY_ID|$FUNC_IDENTITY_ID|" /tmp/patch.json

# 3. パッチ適用
az resource patch \
  -g rg-acme-poc \
  -n func-acme-poc \
  --resource-type "Microsoft.Web/sites" \
  --properties @/tmp/patch.json

# 4. 状態確認(Runningになっているか、実際にACRイメージをpullできているか)
az functionapp show -g rg-acme-poc -n func-acme-poc --query "{state:state, kind:kind}" -o json
az webapp log deployment show -g rg-acme-poc -n func-acme-poc
```

## ハマりどころ(この手順に至るまでの経緯、要点だけ)

- `--image`にプライベートACRイメージを指定して作成すると、マネージドIDが未割当の状態でもCLIがadmin認証情報を
  自動lookupしにいき、admin無効の場合はエラーになる。これは「マネージドIDのRBAC経路が失敗した」のではなく、
  「そもそもその経路を通っていない」だけなので注意。
- `--image`を省略しての新規作成は、公式ドキュメントの案内と異なり現行CLI(2.71.0)では失敗した。プレースホルダー
  イメージでの作成→後からの切り替え、という回避策で対応。
- `az resource patch`にインラインでJSONを渡すと、zshのエスケープが崩れて`Error parsing JSON`になることがある。
  `--properties @ファイルパス`の形でファイル経由にするのが安全。
- `DOCKER_REGISTRY_SERVER_URL`はホスト名のみ(`acr.azurecr.io`)。`https://`を付けると
  `ContainerAppInvalidRegistryServerValue`でARM側から拒否される。

## マネージドIDの追加設定(必要な場合)

```
FUNC_IDENTITY_ID=$(az identity show -g rg-acme-poc -n id-func-poc --query id -o tsv)
FUNC_CLIENT_ID=$(az identity show -g rg-acme-poc -n id-func-poc --query clientId -o tsv)

az functionapp config appsettings set -g rg-acme-poc -n func-acme-poc --settings \
  AZURE_CLIENT_ID="$FUNC_CLIENT_ID" \
  ACME_DOMAIN="acme-test.covesun.net" \
  ACME_EMAIL="<Pのメール>" \
  ACME_SERVER="https://acme-staging-v02.api.letsencrypt.org/directory" \
  CHALLENGE_STORAGE_ACCOUNT="<storageAccountName>" \
  KEYVAULT_NAME="kv-acme-poc" \
  KEYVAULT_CERT_NAME="acme-test-covesun-net" \
  ACS_ENDPOINT="<ACSのエンドポイント>" \
  ACS_SENDER_ADDRESS="<ACS送信元アドレス>" \
  NOTIFY_TO_ADDRESS="<通知先メール>"
```

## 手動実行(テスト)

Timerトリガーの本番実行を待たずに、初回はAzure Portalの「コード + テスト」からCertbotRenew関数を手動起動するか、
`az functionapp function invoke`相当の操作で試すのが早い。うまくいけば`/etc/letsencrypt/live/acme-test.covesun.net/`
配下に証明書一式ができ、`deploy-hook.sh`がKey Vaultへのimportとメール通知まで実行する。

## 未確認事項

- ACS EmailをマネージドID認証で叩くために必要な正確なロール名(要確認、`id-func-poc`への追加割り当てが要る)
- ACR pullをFunctions on ACAがマネージドIDで行う場合の設定方法(ACRの`AcrPull`ロールを`id-func-poc`へ付与する形になるはず、要検証)

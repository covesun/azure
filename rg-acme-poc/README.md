# ACME(Let's Encrypt) PoC — ARMテンプレート一式

covesun.net (`acme-test.covesun.net`) を使った、AGW + Key Vault(RBAC/PE限定) + Functions on ACA(certbot)
による証明書自動更新の検証環境。staging証明書での一連の自動更新フロー(HTTP-01検証→Key Vaultへ
import→ACS Emailで結果通知→AGWでの証明書配信)を実機で確認済み。

**Key Vaultはpublicネットワークアクセス無効(PE限定)を貫くため、ダミー証明書の手動シードは行わない。**
代わりにAGWを2段階でデプロイし、先に本物のLet's Encrypt証明書をKey Vaultへ入れてから443リスナーを足す。

## デプロイ手順

### 0. 事前準備
- リソースグループ作成
  ```
  az group create -n rg-acme-poc -l japaneast
  ```
- Cloudflareの`acme-test.covesun.net`のAレコードは、フェーズ1で払い出される`pip-agw-poc`のIPが確定してから
  設定する(DNS onlyモードで。プロキシ化するとHTTP-01検証がAGWまで届かず失敗する)

### 1. フェーズ1: 基盤リソースをデプロイ
```
az deployment group create \
  -g rg-acme-poc \
  -f 01-foundation.json \
  -p 01-foundation.parameters.json
```
VNet / サブネット / NSG / Public IP / Key Vault(RBAC, PE限定) / Storage / ACA環境(Consumption, internal限定) /
ACR(Basic, admin無効・RBACのみ) / ACS Email(AzureManagedDomain) / 2つのユーザー割り当てID / RBACロール割り当て
まで一括(ACR・ACSも含めて全て「基盤」としてここにまとめている)。

**デプロイ後、ACS関連の値を控えておく**(Functions on ACAの環境変数設定で使う):
```
# ACSのメール送信エンドポイント
az communication list -g rg-acme-poc --query "[0].hostName" -o tsv
# → https://<ホスト名> の形にして ACS_ENDPOINT に使う

# AzureManagedDomainの実際のFQDN(送信元アドレスのドメイン部分)
az communication email domain show \
  -g rg-acme-poc \
  --email-service-name acs-email-acme-poc \
  --domain-name AzureManagedDomain \
  --query "fromSenderDomain" -o tsv
# → DoNotReply@<この値> が ACS_SENDER_ADDRESS
```

**自分自身のアカウントにも以下2つのロールを付与しておくこと**(証明書の中身確認・チャレンジ用blobの手動操作に
必要。自動化の実行主体である`id-func-poc`には`01-foundation.json`側で既に付与済み):
```
MY_OID=$(az ad signed-in-user show --query id -o tsv)
KV_ID=$(az keyvault show -n kv-acme-poc -g rg-acme-poc --query id -o tsv)
STORAGE_ID=$(az storage account show -n <storageAccountName> -g rg-acme-poc --query id -o tsv)

az role assignment create --role "Key Vault Certificates Officer" \
  --assignee-object-id "$MY_OID" --assignee-principal-type User --scope "$KV_ID"
az role assignment create --role "Storage Blob Data Contributor" \
  --assignee-object-id "$MY_OID" --assignee-principal-type User --scope "$STORAGE_ID"
```
ただしKey Vaultはpublicアクセス無効のため、このロールがあっても**自分のMacから直接Key Vaultのデータプレーン
(証明書一覧・取得など)は叩けない**。確認作業が要る場合はAzure Portal(ブラウザ経由、Azure内部から到達)か、
VNet内のリソース経由で行うこと(Storageの`$web`コンテナはpublic blob accessが有効なのでデータプレーン操作は
問題なく可能)。

### 2. Storageの静的Webサイトを有効化 + ヘルスチェック用ファイルを配置(手動 — ARMに対応リソースが無いため)
```
az storage blob service-properties update \
  --account-name <01-foundation.parameters.json の storageAccountName> \
  --static-website \
  --404-document 404.html \
  --index-document index.html \
  --auth-mode login
```
有効化しただけでは`$web`コンテナに実体ファイルが1つも無く、ルート(`/`)へのアクセスが404を返し続けて
**AGWのヘルスプローブが恒久的に「異常」判定になる**(実際にこれでハマった)。そのため、プローブ用の
ダミーファイルを必ず置いておく:
```
echo "ok" | az storage blob upload \
  --account-name <storageAccountName> \
  --container-name '$web' \
  --name "healthz" \
  --file /dev/stdin \
  --auth-mode login \
  --overwrite
```
(`02a-appgateway-bootstrap.json`側のカスタムプローブが`/healthz`を見る設定になっている)

有効化後、`az storage account show -n <storageAccountName> --query "primaryEndpoints.web"`で
静的サイトのエンドポイントURLを控え、`https://`を除いたホスト名部分だけ抜き出しておく
(フェーズ2a/2bの`challengeBackendFqdn`パラメータに使う)。

### 3. フェーズ2a: AGWを80番リスナーのみでブートストラップ
```
az deployment group create \
  -g rg-acme-poc \
  -f 02a-appgateway-bootstrap.json \
  -p 02a-appgateway-bootstrap.parameters.json
```
80番リスナー + `/.well-known/acme-challenge/*`のパスルーティング + `bhs-challenge`用のカスタムヘルス
プローブ(`/healthz`、200-399を正常とみなす)の最小構成。Key Vault証明書への参照は一切含まないので、
ダミー証明書は不要。

デプロイ後、`pip-agw-poc`のIPを確認してCloudflareのAレコードを設定する。バックエンドの健全性は
以下で確認できる(`Healthy`になっていること。`Unhealthy`ならステップ2の`healthz`配置漏れを疑う):
```
az network application-gateway show-backend-health -g rg-acme-poc -n agw-poc -o json
```

### 4. Functions on ACA(certbot)のビルド・デプロイ、本物の証明書を取得
Functions on Container Appsはまだ新しめのオファリングでARMスキーマの変動が大きいため、
このテンプレート一式には含めていない。詳細は `functions-on-aca/README.md` を参照(トラブルシュート込みの
詳しい版はそちら)。ここに実行するコマンドをそのまま書く。成功すると、certbotがHTTP-01検証 → Key Vaultへ
証明書importまでを実行し、Key Vault内に`acme-test-covesun-net`という名前の証明書オブジェクトが実在する
状態になる。

1. イメージをビルドしてACRへpush
   ```
   cd functions-on-aca
   az acr login -n acracmepoc6mgbgs
   docker build -t acracmepoc6mgbgs.azurecr.io/acme-certbot:latest .
   docker push acracmepoc6mgbgs.azurecr.io/acme-certbot:latest
   ```
2. Function Appを**公開のプレースホルダーイメージでいったん作成**(プライベートACRイメージを最初から
   指定すると、マネージドID未割当の状態でadmin認証情報の自動lookupが走り、admin無効のため失敗するため。
   admin再有効化はしないこと)
   ```
   FUNC_IDENTITY_ID=$(az identity show -g rg-acme-poc -n id-func-poc --query id -o tsv)

   az functionapp create \
     -g rg-acme-poc \
     -n func-acme-poc \
     --environment env-acme-poc \
     --storage-account <01-foundation.parameters.json の storageAccountName> \
     --functions-version 4 \
     --image mcr.microsoft.com/azure-functions/dotnet8-quickstart-demo:1.0 \
     --assign-identity "$FUNC_IDENTITY_ID"
   ```
3. 本物のACRイメージ+マネージドID認証に切り替え(JSONは必ずファイル経由で渡す。
   `DOCKER_REGISTRY_SERVER_URL`はホスト名のみ、`https://`は付けない)
   ```
   cat > /tmp/patch.json << 'EOF'
   {
     "siteConfig": {
       "linuxFxVersion": "DOCKER|acracmepoc6mgbgs.azurecr.io/acme-certbot:latest",
       "acrUseManagedIdentityCreds": true,
       "acrUserManagedIdentityID": "REPLACE_WITH_IDENTITY_ID",
       "appSettings": [
         { "name": "DOCKER_REGISTRY_SERVER_URL", "value": "acracmepoc6mgbgs.azurecr.io" }
       ]
     }
   }
   EOF
   sed -i '' "s|REPLACE_WITH_IDENTITY_ID|$FUNC_IDENTITY_ID|" /tmp/patch.json

   az resource patch \
     -g rg-acme-poc \
     -n func-acme-poc \
     --resource-type "Microsoft.Web/sites" \
     --properties @/tmp/patch.json

   az functionapp show -g rg-acme-poc -n func-acme-poc --query "{state:state, kind:kind}" -o json
   ```
   **注意**: この`az resource patch`は`siteConfig.appSettings`配列を丸ごと置き換える。この時点では
   まだ他の環境変数を設定していないので問題ないが、**この後は`az resource patch`でappSettingsを
   触らないこと**(App Insights接続文字列などが消える事故が実際に起きた)。以後の環境変数変更は
   必ず`az functionapp config appsettings set`(マージされる)を使う。

4. 環境変数を設定
   ```
   FUNC_CLIENT_ID=$(az identity show -g rg-acme-poc -n id-func-poc --query clientId -o tsv)

   az functionapp config appsettings set -g rg-acme-poc -n func-acme-poc --settings \
     AZURE_CLIENT_ID="$FUNC_CLIENT_ID" \
     ACME_DOMAIN="acme-test.covesun.net" \
     ACME_EMAIL="<メールアドレス>" \
     ACME_SERVER="https://acme-staging-v02.api.letsencrypt.org/directory" \
     CHALLENGE_STORAGE_ACCOUNT="<01-foundation.parameters.json の storageAccountName>" \
     KEYVAULT_NAME="kv-acme-poc" \
     KEYVAULT_CERT_NAME="acme-test-covesun-net" \
     ACS_ENDPOINT="https://<ステップ1で控えたhostName>" \
     ACS_SENDER_ADDRESS="DoNotReply@<ステップ1で控えたドメイン>" \
     NOTIFY_TO_ADDRESS="<メールアドレス>" \
     CERTBOT_SCHEDULE="0 0 3 * * *"
   ```
   `CERTBOT_SCHEDULE`はNCRONTAB形式(`function_app.py`が`%CERTBOT_SCHEDULE%`という形で参照している。
   app settingに実体が無いと実行時エラーになるので必須)。

   **設定後は必ず値が空文字列になっていないか確認すること**(`$(...)`の取得コマンドが失敗して空を
   渡してしまうと、`appsettings set`はエラーを出さず空文字列で登録される。特に`AZURE_CLIENT_ID`は
   これで一度事故った):
   ```
   az functionapp config appsettings list -g rg-acme-poc -n func-acme-poc \
     --query "[?name=='AZURE_CLIENT_ID'].value" -o tsv
   ```

5. **手動実行(テスト)**: ACA環境が`internal: true`(VNet内限定)のため、Portalの「コード+テスト」も
   VNet外からのcurlも、default hostnameの名前解決すら失敗して使えない。また`az functionapp restart`は
   Functions on ACAでは`BadRequest: This is currently not supported`で使えない。Timerトリガー自体は
   ACAのスケールコントローラーが内部的に発火させる仕組みでingressと無関係なので、`CERTBOT_RUN_ON_STARTUP`を
   立てて**`stop`→`start`**でコンテナを作り直して即時実行させる:
   ```
   az functionapp config appsettings set -g rg-acme-poc -n func-acme-poc --settings \
     CERTBOT_RUN_ON_STARTUP="true"

   az functionapp stop -g rg-acme-poc -n func-acme-poc
   az functionapp start -g rg-acme-poc -n func-acme-poc
   ```
   ログは`az webapp log tail`もKudu依存で使えないため、Application Insights(自動作成される。
   ワークスペースベースなので`AppTraces`テーブルで見る)経由で確認する:
   ```
   WORKSPACE_ID=$(az monitor log-analytics workspace show \
     --ids "$(az monitor app-insights component show -g rg-acme-poc -a func-acme-poc --query workspaceResourceId -o tsv)" \
     --query customerId -o tsv)

   az monitor log-analytics query -w "$WORKSPACE_ID" \
     --analytics-query "AppTraces | where TimeGenerated > ago(15m) | order by TimeGenerated asc | project TimeGenerated, Message" \
     -o table
   ```
   成功時は`certbot renewal run: succeeded`相当のログと、ACSからのメール受信で確認できる。
   確認できたら、以後の意図しない再実行を防ぐため元に戻す:
   ```
   az functionapp config appsettings set -g rg-acme-poc -n func-acme-poc --settings \
     CERTBOT_RUN_ON_STARTUP="false"
   ```
6. Key Vaultに`acme-test-covesun-net`証明書オブジェクトが実在することを確認できたら次のステップへ
   ```
   az keyvault certificate show --vault-name kv-acme-poc --name acme-test-covesun-net \
     --query "{subject:policy.x509CertificateProperties.subject, notAfter:attributes.expires}"
   ```

### 5. フェーズ2b: AGWに443リスナーを追加(最終形へ収束)

`02-appgateway.parameters.json`の4つのプレースホルダーに実際の値を埋める:
```
az network vnet subnet show -g rg-acme-poc --vnet-name vnet-acme-poc -n snet-agw --query id -o tsv
# → agwSubnetId
az network public-ip show -g rg-acme-poc -n pip-agw-poc --query id -o tsv
# → publicIpId
az identity show -g rg-acme-poc -n id-agw-poc --query id -o tsv
# → agwIdentityId
az storage account show -n <storageAccountName> --query "primaryEndpoints.web" -o tsv
# → challengeBackendFqdn(https://と末尾の/を除いたホスト名部分のみ)
```

デプロイ:
```
az deployment group create \
  -g rg-acme-poc \
  -f 02-appgateway.json \
  -p 02-appgateway.parameters.json
```
`02-appgateway.json`は80番(恒久維持)+443番(Key Vault証明書参照)の完全な最終状態。
同じAGWリソース名に対して再デプロイすることで、80番はそのまま、443番が追加される形になる。
80番は自動更新のたびにHTTP-01検証で使い続けるので、恒久的に残す。

**443番リスナーは実コンテンツを返すバックエンドを持たない**(`bap-placeholder`は空)ため、
`https://acme-test.covesun.net/`へのアクセスは**TLSハンドシェイク成功後に502が返るのが正しい挙動**。
確認すべきはTLSハンドシェイクで正しい証明書が提示されるかどうかのみ:
```
curl -vk https://acme-test.covesun.net/ 2>&1 | grep -A 3 "subject:\|issuer:"
```
またはブラウザでアクセスし、警告画面の「証明書の詳細」から発行者が`(STAGING) Let's Encrypt`、
CNが`acme-test.covesun.net`になっていることを確認する(stagingのCAはどのブラウザの信頼ストアにも
入っていないため、警告が出ること自体が正常)。

## Let's Encrypt → GMOグローバルサインへの切り替えで変わる点

このPoCはLet's Encryptを無料の代替として使って検証したが、本番ではGMOグローバルサインのACMEサーバーに
差し替える想定。アーキテクチャ(AGW + Key Vault PE限定 + Functions on ACA + マネージドID + ACS Email通知)
自体は変わらない前提。変更が必要になりそうな点を整理する。

- **`ACME_SERVER`環境変数の差し替え**: `run.sh`が`--server "$ACME_SERVER"`という形でcertbotに渡している
  だけなので、GMOのACMEディレクトリURLに変えるだけで済む見込み。ただし実際のURLはGMOとの契約情報から
  取得する必要があり、このPoCではまだ確認できていない。
- **EAB(External Account Binding)が必要になる可能性が高い**: Let's Encryptはアカウント登録にEAB不要だが、
  商用CAの多くはKID/HMACキーによるEAB認証を要求する。certbotは`--eab-kid`/`--eab-hmac-key`オプションで
  対応可能だが、①KID/HMACキーをどう安全に保持するか(Key Vaultにシークレットとして格納し、実行時に
  マネージドID経由で取得する形が妥当と思われる)、②`run.sh`/hookスクリプトへのオプション追加、
  の2点は未実装・未検証。
- **DV(ドメイン認証)とOV(組織認証)の差**: Let's EncryptはDVのみで、HTTP-01のドメイン所有確認だけで
  完全自動発行できる。GMOグローバルサインの契約がOV証明書の場合、初回発行時(または更新のたびに)
  組織の実在性審査が人手で挟まる可能性があり、その場合は**完全自動更新ができない**リスクがある。
  契約プランがDVかOVかを事前に確認する必要がある(未確認)。
- **証明書の有効期間とスケジュール**: Let's Encryptは90日固定だが、商用CAは1年など長期の場合が多い。
  有効期間が変わる場合、`CERTBOT_SCHEDULE`(現状は`0 0 3 * * *`で毎日実行し、certbotが期限間近かどうかを
  内部判定)自体は毎日実行のままで動くはずだが、実際の更新間隔・レート制限の仕様差は要確認。
- **レート制限**: Let's Encrypt staging/productionそれぞれ独自のレート制限があるが、GMO側のレート制限は
  未確認。テスト時の連続リトライ回数に注意が必要。
- **通知まわりは変更不要**: 成功/失敗の通知はACS Email側(`notify.py`)で独自に行っており、CA自体の
  通知機能には依存していないため、CA切り替えによる影響はない。
- **証明書のissuer/CN表示**: staging証明書は`(STAGING) Let's Encrypt`のようにブラウザから警告が出るが、
  GMOの証明書は正規のパブリックCAとして信頼される想定(要・実機確認)。

## 要検証・要確認として残っている点

- ACS Email送信に必要なロール「Communication and Email Service Owner」が、実際にメール送信アクションを
  含むかどうか(ロール一覧サイト2件から拾ったGUIDで、公式ドキュメントのページ内容までは確証が取れていない。
  実際にはメール受信まで確認済みなので、少なくとも動作上は問題ない)
- Functions on ACAのARMスキーマ(上記の理由でCLIに逃がしている)
- Let's Encrypt staging証明書での一連の流れは確認済み。production証明書での実行確認がまだ
- GMOグローバルサインへの切り替え詳細は上記「Let's Encrypt → GMOグローバルサインへの切り替えで変わる点」参照(EAB要否・OV/DV・実際のACMEサーバーURLはいずれも未確認)
- PoC完了後、AGW・Public IPなど公開系リソースの削除(コスト・露出面の観点で推奨)

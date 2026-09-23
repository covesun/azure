# Functions on ACA — certbotコンテナ(Storage不使用版)

`rg-acme-poc`(Blob版)との違いだけをまとめる。共通の手順は`../../README.md`(Blob版)を参照。

## Blob版との違い

- チャレンジ応答をStorageの`$web`静的Webサイトに置く代わりに、**同じコンテナ内のFunction App自身**が
  `/.well-known/acme-challenge/{token}`というHTTPルートで配信する(`function_app.py`の`AcmeChallenge`関数)。
- `auth-hook.sh`/`cleanup-hook.sh`はStorageを一切触らない(`az login`も不要)。ローカルディスク
  (`/tmp/acme-challenge/`)にファイルを書く/消すだけ。
- そのため`CHALLENGE_STORAGE_ACCOUNT`という環境変数はもう存在しない。
- **Function Appは`minReplicas`/`maxReplicas`を両方1に固定するのが前提**。auth-hookが書いたファイルを
  同じインスタンスのAcmeChallenge関数が読む必要があるため、複数レプリカに分散すると壊れる
  (更新頻度の低い自動化ジョブなのでスケールしない制約は実害にならない)。
- `AcmeChallenge`と`healthz`のHTTPルートは`auth_level=ANONYMOUS`必須。CA(Let's Encrypt)からのアクセスに
  function keyは付かないので、既定のfunction-levelのままだと401で弾かれて検証が失敗する。

## 必要な環境変数(Function App の設定に追加)

| 変数名 | 用途 | 例 |
|---|---|---|
| `ACME_DOMAIN` | 発行対象ドメイン | `acme-test2.covesun.net` |
| `ACME_EMAIL` | ACMEアカウントのメール | (Pのメールアドレス) |
| `ACME_SERVER` | ACMEディレクトリURL | `https://acme-staging-v02.api.letsencrypt.org/directory`(最初はstaging) |
| `KEYVAULT_NAME` | 証明書インポート先Key Vault | `kv-acme-poc` |
| `KEYVAULT_CERT_NAME` | Key Vault内の証明書オブジェクト名 | `acme-test2-covesun-net` |
| `ACS_ENDPOINT` | Azure Communication ServicesのEmail用エンドポイント | `https://<acs-resource>.communication.azure.com` |
| `ACS_SENDER_ADDRESS` | ACSの検証済み送信元アドレス | `DoNotReply@xxxx.azurecomm.net` |
| `NOTIFY_TO_ADDRESS` | 通知先メールアドレス | (Pのメールアドレス) |
| `AZURE_CLIENT_ID` | `id-func-poc`のクライアントID | `az identity show`で取得 |
| `CERTBOT_SCHEDULE` | Timerトリガーのスケジュール(NCRONTAB) | `0 0 3 * * *` |
| `CERTBOT_RUN_ON_STARTUP` | `true`でコンテナ起動時に即時実行(手動テスト用) | `true`/`false` |

(`CHALLENGE_STORAGE_ACCOUNT`はこの版では不要 — Blob版との唯一の差分)

## ビルド・Function App作成

Blob版と同じ手順(`../../README.md`のステップ4-1〜4-3相当)。ACR名・Function App名・Storage名(Functions
ランタイム用の裏側ストレージ。チャレンジ配信には使わない)をこのrg用に読み替えるだけ。

```
cd functions-on-aca
az acr login -n <このrgのACR名>
docker build -t <このrgのACR名>.azurecr.io/acme-certbot:latest .
docker push <このrgのACR名>.azurecr.io/acme-certbot:latest
```

## レプリカ数固定(この版だけの追加手順)

Function App作成・イメージ切り替え後に、Container Appのスケール設定を1/1に固定する:

```
az containerapp update \
  -g rg-acme-poc-nostorage \
  -n func-acme-poc-li0cfu \
  --min-replicas 1 \
  --max-replicas 1
```

(Functions on ACAはContainer Appsの上に構築されているため、`az containerapp update`でスケール設定を
直接触れる。未検証: `az functionapp`側のコマンドで同等の設定ができるかは確認していない)

## AGWのバックエンドFQDN

Blob版の「Storage静的サイトのホスト名」の代わりに、Function Appのdefault hostnameを使う:

```
az functionapp show -g rg-acme-poc-nostorage -n func-acme-poc-li0cfu --query defaultHostName -o tsv
```

これを`02a-appgateway-bootstrap.parameters.json`/`02-appgateway.parameters.json`の
`challengeBackendFqdn`に設定する。

## 未確認事項(この版特有)

- ACA環境(`internal: true`)配下のFunction Appに対して、AGWのバックエンドHTTP設定
  (`pickHostNameFromBackendAddress`、ポート443/HTTPS)がそのまま疎通するか — 実機未検証。
  Blob版のStorage静的サイトとは証明書・TLS終端の実装が異なる(ACAのマネージド証明書)ため、
  念のためAGW側のバックエンドヘルスプローブ結果を必ず確認すること。
- `az containerapp update --min-replicas 1 --max-replicas 1`がFunctions on ACAに対して
  正しく効くか(スケールがTimerトリガーの発火やコールドスタートに影響しないか)は要検証。

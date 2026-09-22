# オンプレミス AD DS × Azure Files (Private Endpoint) 検証環境 ARM テンプレート

添付の検証手順書（Zenn記事連動）に記載されたリソース構成を、コスト最小限で一括構築するための ARM テンプレートです。

## 1. 含まれるファイル

| ファイル | 内容 |
|---|---|
| `azuredeploy.json` | メインの ARM テンプレート |
| `azuredeploy.parameters.covesun-net.json` | パラメーターファイル（ドメイン名 `corp.covesun.net` シナリオ。検証済みドメインのサブドメイン方式） |
| `azuredeploy.parameters.local.json` | パラメーターファイル（ドメイン名 `covesun.local` シナリオ。実際の社内AD構成に近い方式） |
| `README.md` | 本ファイル |

2つのパラメーターファイルがあるのは、**学習目的で2パターンのドメイン名を比較検証するため**です（5-0節参照）。`adminPassword` はどちらのファイルにも平文で書かず、デプロイ時にコマンドラインから渡してください。

## 2. デプロイ手順

どちらか一方のシナリオを選んでデプロイします。**2つを同時にデプロイするとVM等のリソースが倍になりコストも倍になるため、片方を検証し終えたらリソースグループごと削除してから、もう片方をデプロイすることを推奨します**（5-0節に比較の進め方を記載）。

### シナリオA: `corp.covesun.net`（検証済みサブドメイン方式）

```bash
az group create --name rg-azurefiles-adds-demo --location japaneast

az deployment group create \
  --resource-group rg-azurefiles-adds-demo \
  --template-file azuredeploy.json \
  --parameters azuredeploy.parameters.covesun-net.json \
  --parameters adminPassword='<強力なパスワード>'
```

### シナリオB: `covesun.local`（実運用のAD構成に近い方式）

```bash
az group create --name rg-azurefiles-adds-demo --location japaneast

az deployment group create \
  --resource-group rg-azurefiles-adds-demo \
  --template-file azuredeploy.json \
  --parameters azuredeploy.parameters.local.json \
  --parameters adminPassword='<強力なパスワード>'
```

シナリオを切り替える際は、前のリソースグループを削除してから新しいシナリオをデプロイしてください（AD DSのフォレスト名はドメインコントローラー昇格時に確定するため、既存環境のドメイン名だけを後から変更することはできません）。

```bash
az group delete --name rg-azurefiles-adds-demo --yes
```

`adminPassword` はパラメーターファイルに平文で書かず、上記のように `--parameters` でコマンドラインから渡すか、Key Vault 参照を利用してください。デプロイ完了まで概ね20〜30分程度（AD DS フォレスト作成の自動再起動を含む）を想定しています。

デプロイ後、`az deployment group show` の `outputs` からストレージアカウント名などを取得できます。

## 3. コスト最小化のための設計判断

ヒアリングで確認した内容を反映しています。

- **ストレージ冗長: Standard_LRS（既定値）** — 元記事の GZRS ではなく LRS を既定にしています。AD DS認証・DFS-N・Entra Connect の検証手順はレプリケーション方式に依存しないため、ゾーン/geo冗長のコストは不要と判断しました。GZRS で厳密に再現したい場合は `storageAccountSkuName` パラメーターを変更してください。
- **Azure Bastion: Developer SKU（既定値）** — 専用サブネット・Public IP が不要で無料です。VM への Portal 経由アクセスのみで検証手順（Step 2〜6 の PowerShell 操作、Step 6 方法B の Edge ブラウザ操作）は問題なく実施できます。**注意点は 5-3 を参照してください。**
- **VM サイズ: Standard_B2s（既定値）** — バースト型の最小構成。AD DS ドメインコントローラー1台・ドメイン参加クライアント1台程度の検証負荷であれば十分です。
- **VM 自動シャットダウン: 有効（既定 22:00 JST）** — `Microsoft.DevTestLab/schedules` により両VMを毎日自動停止し、検証作業をしない時間帯のコンピュート課金を抑えます。手動で起動すれば作業を再開できます。
- **OS ディスク: Standard HDD (Standard_LRS)** — Premium SSD ではなく最安のマネージドディスクを使用しています。
- **Public IP: 使用しない** — 両VM・Bastion(Developer SKU) ともに Public IP を割り当てていません（元記事の要件どおり）。

## 4. リソース構成と元記事との対応

| リソース | テンプレート内の名称 | 備考 |
|---|---|---|
| VNet | `vnet-azurefiles-adds` (10.0.0.0/16) | Subnet-DC / Subnet-Client / Subnet-PE。DHCP DNS設定を `10.0.1.4` (DC) に固定 |
| DC VM | `vm-dc01` | Windows Server 2022、固定IP `10.0.1.4`、`CreateADForest` 拡張機能で `domainName` パラメーターのフォレストを自動作成（シナリオAは`corp.covesun.net`、シナリオBは`covesun.local`） |
| Client VM | `vm-client01` | Windows Server 2022、`JoinADDomain` 拡張機能で自動ドメイン参加 |
| Storage Account | `stazfiles<unique>` | StorageV2 / パラメーター化されたレプリケーション（既定LRS） |
| Azure Files | `file-share-sales` | プロビジョニング v2 (HDD)、32 GiB / 500 IOPS / 60 MiB/s |
| Private Endpoint + DNS | `<storage>-pe` / `privatelink.file.core.windows.net` | VNet にリンク済み |
| Azure Bastion | `bastion-azurefiles-adds` | Developer SKU（Standard切替可） |

## 5. 重要な注意点・前提（必ず確認してください）

### 5-0. ドメイン名を2パターン用意している理由（学習用比較）

元記事の既定ドメイン名は `corp.contoso.com` でしたが、学習目的で以下の2シナリオを用意しています。ハイブリッドID構成でドメイン名の選び方がEntra Connectの挙動にどう影響するかを、実際に両方デプロイして比較できます。

| | シナリオA: `corp.covesun.net` | シナリオB: `covesun.local` |
|---|---|---|
| 位置づけ | 検証済みドメイン(covesun.net)のサブドメイン | 実際の社内AD構成でよくある `.local` 方式 |
| Entra IDでの検証可否 | サブドメインのため自動検証される | `.local` は内部予約名のため構造的に検証不可 |
| Entra Connectセットアップ時 | 「検証済みドメインと一致させずに続行する」チェック不要 | 同チェックが必要（手順書 Step 6-2-5） |
| 同期後のUPN | `user@corp.covesun.net` のまま | `user@<テナント名>.onmicrosoft.com` に読み替えられる |
| Microsoftの現在の推奨 | 推奨されるパターン | 新規構築では非推奨（mDNS競合・証明書発行不可などの理由） |

**シナリオAの補足**: `covesun.net` そのもの（サブドメインを付けないネイキッドドメイン）をフォレスト名にするのは避けてください。DC がそのドメイン名のDNSゾーンに対して権威を持つ構成になるため、実際に公開している `covesun.net` のDNSレコードと内部的に競合する「スプリットブレインDNS」状態を招く可能性があります。

**シナリオBを選ぶ理由**: `.local` はMicrosoftが新規構築では非推奨としていますが、実際の社内ADが `.local` 構成であれば、その制約（UPN不一致、Entra Connectでの回避手順）を検証環境で先に体験しておく価値があります。

**比較のやり方**: シナリオAをデプロイしてStep 6まで実施 → UPNが `user@corp.covesun.net` のまま同期されることを確認 → リソースグループを削除 → シナリオBをデプロイしてStep 6まで実施 → 「続行する」チェックが必要になることと、UPNが `.onmicrosoft.com` に読み替えられることを確認、という流れがおすすめです。

### 5-1. AD DS フォレスト作成の実装方法について

元記事は `CreateADForest` という名前の拡張機能を前提としていますが、本テンプレートでは外部リポジトリ（GitHub上のDSC構成zip）への依存を避け、**自己完結した CustomScriptExtension** で同名の拡張機能 `CreateADForest` を実装しています（`Install-ADDSForest` を実行し、拡張機能の完了報告後に30秒遅延で再起動）。これにより単一ファイルでの完全なデプロイが可能ですが、以下は自作の追加ロジックです。

- **DNSフォワーダーの自動設定**: `privatelink.file.core.windows.net` の条件フォワーダーを Azure DNS (`168.63.129.16`) 向けに自動設定します。**これは元記事に明記されていませんが、VNet の DNS サーバーを DC (`10.0.1.4`) に固定している構成では、この設定がないと Private Endpoint 名前解決（手順書 Step 3-2 の `Resolve-DnsName`）が失敗します。** 動作原理: DC が VNet 全体の DNS を担うため、`privatelink.file.core.windows.net` ゾーン（Azure Private DNS）への問い合わせを Azure 提供 DNS (168.63.129.16) へ転送する必要があるためです。
  - **実際に踏んだ不具合(修正済み)**: このフォワーダー設定スクリプトの冪等性チェックに、当初 `Get-DnsServerConditionalForwarderZone` という**実在しないコマンドレット**を使用していました。DnsServer モジュールには `Add-DnsServerConditionalForwarderZone` はありますが対になる `Get-*` は無く、正しくは `Get-DnsServerZone -Name <ゾーン名>` で存在確認します。存在しないコマンドレットの呼び出しは `-ErrorAction SilentlyContinue` では抑止できない終端エラーになるため、起動時タスクがこの行で毎回失敗し、条件フォワーダーが一度も作成されない状態になっていました(結果として `net use`/`Resolve-DnsName` がプライベートIPではなくパブリックIPに解決される不具合として顕在化)。現在は `Get-DnsServerZone -Name` を使う形に修正済みです。既にデプロイ済みの環境でこの症状が出ている場合は、vm-dc01 上で手動で `Add-DnsServerConditionalForwarderZone -Name "privatelink.file.core.windows.net" -MasterServers 168.63.129.16` を実行すれば回復します。
  - **実装方式に関する注意**: 当初はこれを `CreateADForest` とは別の2つ目の CustomScriptExtension として実装していましたが、**Windows VMでは同一ハンドラー（同じpublisher+type）の拡張機能を1台のVMに複数アタッチできない**という制約があり(`Multiple VMExtensions per handler not supported for OS type 'Windows'`エラー)、デプロイに失敗することが判明しました。そのため現在は、`CreateADForest` 拡張機能の中でDNSフォワーダー設定スクリプトをBase64エンコードした状態でファイルに書き出し、**Windowsのタスクスケジューラに「起動時に実行」するタスクとして登録**する方式に変更しています。これにより、AD DSフォレスト昇格に伴う再起動を挟んでから（再起動後の初回起動時に）DNS/NTDSサービスが起動しきるのを待って条件フォワーダーを設定できます。このタスクは冪等（べき等）に作ってあるため、自動シャットダウンからの再起動のたびに毎回実行されても害はありません（フォワーダーが既に存在する場合はスキップします）。
- `WaitForDomain` 拡張機能（クライアントVM側、`Microsoft.Compute/CustomScriptExtension`）: `JoinADDomain`（`Microsoft.Compute/JsonADDomainExtension`）の前に、ドメインが名前解決できるようになるまで待機するリトライロジックです。ハンドラーの種類が異なるため、クライアントVM側はこの2つを同時にアタッチしても問題ありません。

パスワードに `' " `` $` などの記号を使うと CustomScriptExtension 内のエスケープが崩れる可能性があるため、使用を避けてください。

**セキュリティ上の補足**: `adminPassword`（secureString）は、テンプレート内の `variables`（中間変数）には一切格納せず、各拡張機能の `protectedSettings` 内で直接参照する形にしています。ARM テンプレートの仕様上、secureString から派生した値を `variables` に格納すると、Azure Resource Manager 側でその値がセキュアな値として扱われなくなる（デプロイ履歴等に露出しうる）ため、この設計を採用しています。

### 5-2. Provisioned v2 (HDD) ファイル共有のプロパティについて

`provisionedIops` / `provisionedBandwidthMibps` フィールドは比較的新しい Azure Files の課金モデル（標準/HDDバックエンド共有への Provisioned v2）に対応するものです。API バージョン（`2023-05-01`）やフィールド名の詳細は Azure 側の仕様更新が続いている領域のため、**デプロイ時にエラーが出た場合は Azure Portal または最新の `az storage share-rm create` のドキュメントでスキーマを確認し、必要に応じてテンプレートの該当箇所を調整してください。**

### 5-3. Azure Bastion Developer SKU の制限

- 2026年時点でも Developer SKU は利用可能リージョンが限定される場合があります。`japaneast` でデプロイエラーになった場合は `bastionSkuName` を `Standard` に切り替えてください（`AzureBastionSubnet` と Public IP が自動的に追加されます）。
- Developer SKU はネイティブクライアント接続（RDP/SSHクライアント直接接続）や、ローカルPCとVM間のファイルコピー機能に制限があります。手順書 Step 6 の「方法A: 手元PCでダウンロードしてvm-dc01へコピー」がうまく動かない場合は、「方法B: vm-dc01内のEdgeから直接ダウンロード」を使用してください（これは追加コストなしで動作します）。

### 5-4. VM の既定の送信インターネットアクセスについて

VM には NAT Gateway 等を割り当てておらず、コスト最小化のためサブスクリプションの既定の送信アクセス（Default Outbound Access）に依存しています。手順書 Step 2（AzFilesHybrid モジュールのダウンロード）や Step 6（Microsoft Entra Connect のダウンロード、PowerShell Gallery からのモジュール取得）にはインターネットアクセスが必要です。組織のサブスクリプションでこの既定アクセスが無効化されている場合は、NAT Gateway（時間課金が発生します）の追加が必要になります。

### 5-5. Storage Account の AD DS 連携設定

ARM テンプレートでは `azureFilesIdentityBasedAuthentication` を設定していません。これは `domainGuid` 等のドメイン情報が AD DS フォレスト作成後にしか確定しないためで、手順書 Step 2 の `Join-AzStorageAccount` を実行することで初めて設定されます（元記事の手順どおりです）。

### 5-6. Storage Account の公開ネットワークアクセス

`publicNetworkAccess` を `Disabled` にしています（Private Endpoint 経由のみアクセス可能）。`Connect-AzAccount` や `Join-AzStorageAccount` などの ARM 管理操作はデータプレーンではなく Azure Resource Manager 経由のため、この設定と競合せず動作します。

### 5-7. VMへのログイン・管理者アカウントに関する注意（ハマりやすいポイント）

- `adminUsername`（既定 `azureadmin`）は、Azure が別途新規に作成するローカルアカウントではなく、**各VMのビルトイン Administrator アカウント（RID 500）自体をリネームしたもの**です。VMごとに個別にプロビジョニングされるため、vm-dc01 の azureadmin と vm-client01 の azureadmin は名前とパスワードが同じでも実体は別々のアカウントです。
- vm-dc01 を AD DS フォレストの最初のドメインコントローラーに昇格すると、Windows の仕様上「昇格前のビルトイン Administrator アカウント（名前・パスワードとも）がそのままドメインの Administrator 相当アカウントとして引き継がれる」ため、vm-dc01 側の azureadmin がそのまま **`corp\azureadmin` = ドメイン管理者アカウント** になります。デプロイ時に指定した `adminPassword` でそのままログインでき、追加のユーザー作成は不要です。
- 元記事に登場する `corp\Administrator` は、このテンプレートの構成では**存在しません**（Azure が Administrator を azureadmin にリネーム済みのため）。同等の操作は `corp\azureadmin` で行ってください。
- **ドメインコントローラー（vm-dc01）にはローカルSAMが存在しない**ため、RDPのユーザー名欄に `corp\` を付けずに `azureadmin` とだけ入力しても、解決先がドメインアカウントしかないため自動的に `corp\azureadmin` として認証されます（一見ローカルログインしているように見えて実際はドメインログインです）。紛らわしいので、意図を明確にするため `corp\azureadmin` と明示的に入力することを推奨します。
- 一方 vm-client01 はDCではない通常のドメイン参加マシンのため、ユーザー名解決が曖昧になりえます。ローカルアカウントでログインしたい場合は `.\azureadmin`、ドメインアカウントでログインしたい場合は `corp\azureadmin` と明示してください。

## 6. 検証手順そのもの

デプロイ完了後は、添付いただいた手順書の Step 2〜6 をそのまま実施してください（本テンプレートは Step 1 に相当するリソース構築のみを対象としています）。

## 7. 実機検証で判明した追加の注意点（手順書には無い内容）

Step 2〜6 の手動実施部分（AD グループ・DFS 名前空間の作成、`Join-AzStorageAccount`、RBAC 割り当て、NTFS ACL 設定など）はテンプレート化しておらず手順書どおり手動で行う前提です。以下は、それらを実機で実施した際に判明した、手順書には書かれていないハマりどころと設計上の注意点です。

### 7-1. DFS-N セットアップ時の注意点

- **プレースホルダドメインの取り違え**: 手順書の `New-DfsnFolder` の例示コマンドをそのままコピペすると、ドメイン名部分が元記事の例示ドメインのままになっていることがあります。この状態で実行すると `RPC server unavailable`（`MI RESULT 1722`）で失敗します。実際のドメイン名（本テンプレートなら `corp.covesun.net` または `covesun.local`）に置き換えてください。
- **DFS参照の一時的な失敗**: `Get-DfsnFolder` / `Get-DfsnFolderTarget` でフォルダ設定が `Online` かつ正しいターゲットパスになっているにもかかわらず、`net use \\<ドメイン名>\shares\<フォルダ名>` がエラー67（「ネットワーク名が見つかりません」）になることがありました。名前空間ルート単体、およびターゲット先（Azure Files の FQDN）への直接接続はどちらも正常だったため、**DFSクライアント側の参照キャッシュ（既定 TTL 300秒）に古い失敗結果が残っていた可能性が高い**です（未確定・推測。今回は時間経過後の再試行で自然に解消しました）。再発する場合はクライアント側で `dfsutil /pktflush` を実行してから再試行してください。

### 7-2. AD グループスコープと Entra Connect 同期の実際の挙動

- グループスコープの変換は Global⇄Universal、DomainLocal⇄Universal は可能ですが、**Global→DomainLocal への直接変換は AD 上できません**（必ず Universal を経由します）。
- Domain Local スコープのグループも、Entra Connect のデルタ同期で問題なく Entra ID 側に同期されることを実機で確認済みです（「Domain Local は Entra Connect の同期対象外」という情報は誤りであり、本検証で確認・訂正しました）。
- Entra ID 管理センターで同期結果を確認する際は「グループ」ブレードを見てください（「ユーザー」ブレードにはグループは表示されません）。

### 7-3. Azure Bastion の SKU 切替について

- Developer SKU 利用中に、原因不明の接続断（両VMともRDP不可）が発生することがありました。再起動・停止/起動では復旧せず、アクティビティログ・リソース正常性にも痕跡がなく、根本原因は特定できていません。
- Standard SKU への切替で復旧を確認しました。なお **Developer→Standard へのアップグレードは、削除・再作成せずその場でアップグレード可能**です（事前に `AzureBastionSubnet` と Standard SKU の Public IP を用意しておく必要あり）。一方で **Standard→Developer へのダウングレードは、削除して再作成する以外の方法はサポートされていません**（Microsoft公式ドキュメントに明記）。ダウングレード後も `AzureBastionSubnet` と Public IP はリソースとして残る（課金が続く）ため、不要になれば別途手動削除してください。
- 本リポジトリには、`azuredeploy.json` 本体とは独立して Bastion 関連リソース（サブネット・Public IP・Bastion Host）だけを扱う **`bastion-standalone.json`** を用意しています。これを使えば DC/クライアントVM・AD DS 拡張・Storage・Private Endpoint に一切触れずに Bastion の SKU 切替・削除・再作成ができます。

```bash
az deployment group create \
  --resource-group rg-azurefiles-adds-demo \
  --template-file bastion-standalone.json \
  --parameters bastionSkuName=Standard
```

### 7-4. Azure Files 共有ルートの既定 NTFS ACL について

新規作成したファイル共有のルートには、既定で以下の ACL が設定されています。「何も設定していない」つもりでも、実際は認証済み全ユーザーに書き込み権限が継承される状態になっています。

```
BUILTIN\Administrators:(OI)(CI)(F)
BUILTIN\Users:(RX)
NT AUTHORITY\Authenticated Users:(OI)(CI)(M)
NT AUTHORITY\SYSTEM:(OI)(CI)(F)
CREATOR OWNER:(OI)(CI)(IO)(F)
```

部署ごとにアクセス制御したい場合は、対象フォルダで継承をいったんすべて削除し、`NT AUTHORITY\Authenticated Users` と `BUILTIN\Users` のエントリを削除したうえで、部署専用のグループにのみ許可を設定し直してください。

### 7-5. Azure Files の RBAC ロールの使い分け

- 一般ユーザー: `Storage File Data SMB Share Contributor`（読み書き。ACL変更は不可）、または `Storage File Data SMB Share Reader`（読み取りのみ）
- アクセス権管理者（NTFS ACL を変更する担当者）: `Storage File Data SMB Share Elevated Contributor`。初期の一括ロックダウン作業（7-4）には `Storage File Data SMB Admin`（ストレージアカウントキー相当の権限）がMicrosoft公式ドキュメントで推奨されています。

### 7-6. NTFS ACL で「グループに Deny・特定ユーザーに Allow」は機能しない

グループに Deny、特定ユーザーに Allow を設定しても、エクスプローラー等の標準ツールは DACL を正規順（明示的 Deny → 明示的 Allow の順）に自動整列するため、**Deny が必ず先に評価され Allow は機能しません**。「一部ユーザーだけ許可したい」場合は Deny を使わず、**許可対象のグループ自体を絞る**設計にしてください（広く許可を与えたうえで例外的に Deny で絞る、という設計は避けること）。

### 7-7. 未解決のまま持ち越した事項

- あるユーザー（検証中は `SalesUser02`）が AD DS 側では正常に作成されている（DN・UPN とも他の同期済みユーザーと同一 OU で問題なし）にもかかわらず、デルタ同期を実行しても Entra ID 側に一切現れない事象が発生しました。**フル同期（`Start-ADSyncSyncCycle -PolicyType Initial`）を実行したところ同期されることを確認**しましたが、これは「フルで解決した」という事実のみで、**デルタ同期がこの新規ユーザーを拾わなかった根本原因は未特定**です。本来デルタ同期でも新規オブジェクトの追加（Import→Sync→Export）は検知されるはずで、同期ルール変更のような「設定変更」時以外でフル同期が必要になるのは想定外の挙動です。特定のコネクタの実行がスタックしていた、ウォーターマークが不整合になっていた等の可能性がありますが未確認のため、「新規ユーザーは常にフル同期が必要」と一般化しないよう注意してください。次回同様の事象が起きた場合は、フルを試す前に Synchronization Service Manager（`miisclient.exe`）の Operations ログで AD DS コネクタの Delta Import / Sync の実行結果・エラーの有無を確認することを推奨します。
- Azure Bastion Developer SKU での接続断（7-3）についても根本原因は未特定です。

### 7-8. Azure Files のアクセスログでのユーザー識別

AD DS 連携（Kerberos）でアクセスしたユーザーが Azure Files の診断ログ（`StorageFileLogs`）上でどう見えるかを確認しました。

- `StorageFileLogs` にはユーザーの **SID（`SmbPrimarySID`）までしか記録されず、ユーザー名としては保存されません**。ドキュメント上は `RequesterUpn` / `RequesterObjectId` / `RequesterUserName` といったフィールドも存在しますが、オンプレ AD DS の Kerberos 認証リクエストではこれらが埋まらないことがある点に注意してください。
- SID からユーザー名を解決するには、**Microsoft Sentinel を同じ Log Analytics ワークスペースで有効化**し、SentinelのUEBA機能が生成する `IdentityInfo` テーブル（`AccountSID` 列）と `StorageFileLogs` の `SmbPrimarySID` を突き合わせる方法が実務上の定石です。

```kql
let Identities =
    IdentityInfo
    | where isnotempty(AccountSID)
    | summarize arg_max(
        TimeGenerated,
        AccountDisplayName,
        AccountName,
        AccountUPN,
        AccountDomain,
        SourceSystem
    ) by AccountSID;
StorageFileLogs
| where Protocol == "SMB"
| where AuthenticationType =~ "Kerberos"
| join kind=leftouter Identities
    on $left.SmbPrimarySID == $right.AccountSID
| extend User = coalesce(
    RequesterUpn,
    AccountUPN,
    AccountName,
    SmbPrimarySID
)
| project
    TimeGenerated,
    User,
    AccountDisplayName,
    SmbPrimarySID,
    OperationName,
    Uri,
    CallerIpAddress,
    StatusText
| order by TimeGenerated desc
```

- この方法が機能する前提は、**調査対象のユーザーが Entra Connect でハイブリッド同期済み**であること（`IdentityInfo` は Entra ID 側の Sign-in Logs / Audit Logs 由来のデータ連携で生成されるため）。同期されていない AD ユーザーの場合は `IdentityInfo` 側に相当するエントリが無く、この突合方法では解決できません。
- **Microsoft Defender for Identity（MDI）は、この Entra ID 経由の同期パスでは必須ではありません**。[公式ドキュメント](https://learn.microsoft.com/en-us/azure/sentinel/enable-entity-behavior-analytics)によれば、MDI が必須になるのは「オンプレミス AD DS から直接エンティティを同期する」別経路を使う場合（かつ DC への MDI センサー導入が必要）のみで、今回のように Entra Connect でハイブリッド同期済みのユーザーを対象にする分には不要です。UEBA の設定画面で「オンプレミスADから同期する」系のオプションを選ぶと MDI 必須の案内が出ることがあるため、混同しないよう注意してください。

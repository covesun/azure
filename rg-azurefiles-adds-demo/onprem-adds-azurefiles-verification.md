# オンプレミス AD DS × Azure Files (Private Endpoint 閉域接続) 検証環境 ARM テンプレート

本ディレクトリの ARM テンプレートは、Zenn 記事「オンプレミス AD DS と Azure Files を連携させる」の検証手順を実施するための閉域（Private Endpoint）環境を一括構築します。

---

## 1. リソース構成

| リソース種別 | 名称 | 概要・設定 |
|---|---|---|
| 仮想ネットワーク (VNet) | `vnet-azurefiles-adds` | `10.0.0.0/16` |
| サブネット (DC用) | `Subnet-DC` | `10.0.1.0/24` |
| サブネット (Client用) | `Subnet-Client` | `10.0.2.0/24` |
| サブネット (Private Endpoint用) | `Subnet-PE` | `10.0.3.0/24` |
| AD DS ドメインコントローラー VM | `vm-dc01` | Windows Server 2022<br>- 自動的に AD DS フォレスト (`corp.contoso.com`) を作成<br>- IP: `10.0.1.4`（DNSサーバー兼用） |
| 検証クライアント VM | `vm-client01` | Windows Server 2022<br>- 自動的に `corp.contoso.com` ドメインへ参加済み |
| Storage Account | `stazfiles<unique>` | GZRS (`Standard_GZRS`), `StorageV2` |
| Azure Files（ファイル共有） | `file-share-sales` | プロビジョニング v2 (HDD) 仕様<br>- Quota: 32 GiB<br>- IOPS: 500<br>- Bandwidth: 60 MiB/s |
| Private Endpoint & DNS | `stazfiles<unique>-pe`<br>`privatelink.file.core.windows.net` | Azure Files へのアクセスを VNet 内プライベート IP に閉域化 |

VM には Public IP を割り当てません。管理操作は Azure Bastion の Developer SKU または Standard SKU から、VM のプライベート IP 経由で行います。Azure Files への SMB 通信も Private Endpoint 経由です。

---

## 2. ARM テンプレートのデプロイ手順

### Azure CLI を使う場合

```bash
# 1. リソースグループの作成
az group create --name rg-azurefiles-adds-demo --location japaneast

# 2. ARM テンプレートのデプロイ
az deployment group create \
  --resource-group rg-azurefiles-adds-demo \
  --template-file azuredeploy.json \
  --parameters azuredeploy.parameters.json
```

---

## 3. デプロイ後の検証手順 (Zenn 記事連動)

ARM テンプレート完了後、元記事の Step 2（AD DS 連携）および Step 3（権限設定 & マウント）を実施します。

### Step 2-0: vm-dc01 の起動と AD DS サービス確認

`vm-dc01` は、AD DS の役割とドメインコントローラーへの昇格が正常に完了していれば、起動時に必要なサービスを自動起動します。起動直後はサービスが準備中の場合があるため、以下を確認してから次の手順へ進みます。

**1. Azure Portal で VM の状態を確認**

`vm-dc01` が **実行中** であることを確認します。初回構築時は、VM の拡張機能にある `CreateADForest` が成功していることも確認してください。拡張機能が失敗している場合、VM が起動していても AD DS 認証は利用できません。

**2. vm-dc01 上で AD DS 関連サービスを確認**

管理者 PowerShell で以下を実行します。

```powershell
Get-Service NTDS,DNS,Netlogon,Kdc,DFSR |
    Select-Object Name,Status,StartType
```

`NTDS`、`DNS`、`Netlogon`、`Kdc` が `Running` であることを確認します。`vm-dc01` の起動直後は、サービスの起動完了まで少し時間がかかる場合があります。

ドメインコントローラーとして構成されていることは、以下でも確認できます。

```powershell
Get-ADDomain
Get-ADDomainController
```

**3. vm-client01 から DC の DNS とドメイン検出を確認**

`vm-client01` 上で以下を実行します。

```powershell
nslookup corp.contoso.com
nltest /dsgetdc:corp.contoso.com
```

`nltest` がドメインコントローラー情報を返すことを確認します。失敗する場合は、`vm-client01` が `vm-dc01`（`10.0.1.4`）を DNS サーバーとして使用しているか確認し、必要に応じてクライアント VM を再起動します。

**4. vm-client01 をドメイン参加させる**

`vm-client01` がまだワークグループの場合は、Azure Files の検証に使う前にドメイン参加が必要です。現在のローカル管理者（`vm-client01\azureadmin`）で `vm-client01` にログオンし、管理者 PowerShell で実行します。

まず、ドメイン参加状態を確認します。

```powershell
Get-CimInstance Win32_ComputerSystem |
    Select-Object Name,Domain,PartOfDomain
```

`PartOfDomain` が `False` の場合は、ドメイン管理者の資格情報を指定して参加させます。

```powershell
$credential = Get-Credential

Add-Computer `
    -DomainName "corp.contoso.com" `
    -Server "vm-dc01.corp.contoso.com" `
    -Credential $credential `
    -Restart
```

`Get-Credential` には、`vm-dc01` の AD DS に存在するアカウントを指定します。

```
ユーザー名: corp\Administrator
```

再起動後、Bastion のログイン画面でドメインユーザーを指定してログオンします。例として、Step 4 で作成するユーザーを使用できます。

```
ユーザー名: corp\FileTestUser
```

ログオン後、ドメインユーザーでログオンできていることを確認します。

```powershell
whoami
klist
```

`whoami` が `corp\FileTestUser` のように表示されれば、AD DS のドメインユーザーとしてログオンしています。`vm-client01\azureadmin` の場合はローカルユーザーです。

テンプレートの `JoinADDomain` 拡張機能が成功している場合は、この手順は不要です。Azure Portal の `vm-client01` の「拡張機能」で成功状態を確認できない場合は、上記の手動参加を実行してください。

---

## Step 2: Azure Files で AD DS 認証を有効化 (Join-AzStorageAccount)

1. `vm-dc01`（または `vm-client01`）に RDP 接続します（ユーザー: `corp.contoso.com\azureadmin`）。
2. PowerShell を管理者権限で起動し、以下を実行します。

```powershell
# モジュールの準備
Set-ExecutionPolicy -ExecutionPolicy Unrestricted -Scope CurrentUser
Install-Module -Name PowerShellGet -Force
Install-Module -Name Az -Repository PSGallery -Force
Install-WindowsFeature -Name RSAT-AD-PowerShell

# AzFilesHybrid モジュールのダウンロードとインポート
Invoke-WebRequest https://github.com/azure-samples/azure-files-samples/releases/download/v0.2.7/azfileshybrid.zip -OutFile C:\azfileshybrid.zip
Expand-Archive -Path C:\azfileshybrid.zip -DestinationPath C:\azfileshybrid
cd C:\azfileshybrid
.\CopyToPSPath.ps1
Import-Module -Name AzFilesHybrid

# Azure へ接続 & Storage Account を AD DS に参加させる
Connect-AzAccount

$SubscriptionId = "<ご自身のサブスクリプションID>"
$ResourceGroupName = "rg-azurefiles-adds-demo"
$StorageAccountName = "<デプロイされたストレージアカウント名>"

Select-AzSubscription -SubscriptionId $SubscriptionId

Join-AzStorageAccount `
    -ResourceGroupName $ResourceGroupName `
    -StorageAccountName $StorageAccountName `
    -DomainAccountType "ComputerAccount"

# 設定状態の確認
Debug-AzStorageAccountAuth -StorageAccountName $StorageAccountName -ResourceGroupName $ResourceGroupName -Verbose
```

---

## Step 3: ストレージアカウント直接 (UNC パス) アクセスの検証【最優先確認】

DFS-N などの高度な構成を組む前に、まずはクライアント VM から直接ストレージアカウントへアクセス可能かを確認します。

### 1. 共有レベルアクセス権の設定

共有レベル権限を設定するには、以下のいずれかの方法を選択します。

**パターン A: デフォルト共有権限 (DefaultSharePermission) を設定する（推奨・簡易検証用）**

個別の RBAC ロール割り当てを行わずに、AD 認証に成功したすべてのドメインユーザーに一律で共有アクセス権（共同作成者レベル）を許可し、細かい権限は NTFS (ACL) で管理する場合に利用します。PowerShell で以下を実行します。

```powershell
Set-AzStorageAccount `
    -ResourceGroupName "rg-azurefiles-adds-demo" `
    -Name "<ストレージアカウント名>" `
    -DefaultSharePermission "StorageFileDataSmbShareContributor"
```

**パターン B: Azure Portal (IAM) から RBAC ロールを割り当てる**

対象 Storage Account の「アクセス制御 (IAM)」にて、テスト用ユーザー（または Entra ID 同期グループ/自身のアカウント）へ 記憶域ファイル データの SMB 共有の管理者/特権共同作成者 または 閲覧者 を割り当てます。

### 2. Private Endpoint（閉域）DNS 解決確認 (vm-client01 上で実行)

```powershell
Resolve-DnsName <ストレージアカウント名>.file.core.windows.net
# 応答 IP が 10.0.3.x (Subnet-PE のプライベート IP) に解決されていることを確認します
```

### 3. 直接 UNC パスによる SMB マウント確認 (vm-client01 上で実行)

```powershell
net use Z: \\<ストレージアカウント名>.file.core.windows.net\file-share-sales
```

ストレージアカウントキーを指定せず、AD ドメイン認証のみで正常にマウントできることを確認します。

---

## Step 4: AD DS 部署グループによるアクセス制御の検証【実運用想定】

実運用では全社員へ個別ロールを割り当てるのではなく、AD DS の部署セキュリティグループ単位で権限管理を行います。

### 1. AD DS 側で「部署グループ」と「テストユーザー」を作成 (vm-dc01 で実行)

PowerShell を管理者権限で起動し、以下を実行します。

```powershell
# 1. 部署セキュリティグループの作成
New-ADGroup -Name "SG_Sales_Dept" -GroupScope Global -GroupCategory Security
New-ADGroup -Name "SG_HR_Dept" -GroupScope Global -GroupCategory Security

# 2. テスト用ユーザーの作成
$secPassword = ConvertTo-SecureString "P@ssw0rd2026!User" -AsPlainText -Force
New-ADUser -Name "SalesUser01" -SamAccountName "SalesUser01" -UserPrincipalName "SalesUser01@corp.contoso.com" -AccountPassword $secPassword -Enabled $true -ChangePasswordAtLogon $false
New-ADUser -Name "HRUser01" -SamAccountName "HRUser01" -UserPrincipalName "HRUser01@corp.contoso.com" -AccountPassword $secPassword -Enabled $true -ChangePasswordAtLogon $false

# 3. ユーザーをグループに追加
Add-ADGroupMember -Identity "SG_Sales_Dept" -Members "SalesUser01"
Add-ADGroupMember -Identity "SG_HR_Dept" -Members "HRUser01"
```

### 2. Bastion からログオンするための RDP 権限を付与 (vm-client01 で実行)

AD DS のグループに所属していることと、`vm-client01` へ RDP ログオンできることは別の権限です。Bastion からテストユーザーでログオンする場合は、`vm-client01` のローカル管理者（`vm-client01\azureadmin`）で管理者 PowerShell を開き、必要なユーザーを `Remote Desktop Users` に追加します。

```powershell
Add-LocalGroupMember -SID 'S-1-5-32-555' -Member 'CORP\SalesUser01'
Add-LocalGroupMember -SID 'S-1-5-32-555' -Member 'CORP\HRUser01'
```

追加済みのユーザーを確認します。

```powershell
Get-LocalGroupMember -SID 'S-1-5-32-555'
```

### 3. Bastion からドメインユーザーでログオン

`vm-client01` への接続時に、次の形式でログオンします。パスワードは、`New-ADUser` の `-AccountPassword` に指定した値、または `Set-ADAccountPassword` で再設定した値です。

```
ユーザー名: SalesUser01@corp.contoso.com
```

ログオン後、ドメインユーザーであることを確認します。

```powershell
whoami
```

期待値:

```
corp\salesuser01
```

### 4. アクセス制御の評価 (vm-client01 で実行)

1. `SalesUser01` でログオンし、Azure Files のマウントとアクセスを確認します。
2. `HRUser01` でログオンし、`SG_HR_Dept` のユーザーとしてアクセス結果を確認します。
3. 営業部だけにアクセスを許可するフォルダーを作成する場合は、`SG_Sales_Dept` に Windows ACL を設定し、`SG_HR_Dept` ではアクセスできないことを確認します。

```powershell
net use Z: \\stazfilespiwt57c7cgc7s.file.core.windows.net\file-share-sales
whoami
klist
```

注意: `SG_Sales_Dept` と `SG_HR_Dept` は、この Step 4 のコマンドだけでは Azure Files のアクセス可否を分けません。共有レベルは既定の共有権限で許可されているため、部署ごとの差を検証するには、Azure Files 上のフォルダーまたはファイルに Windows ACL を設定してください。

---

## Step 5: DFS ネームスペース (DFS-N) 経由アクセスの検証【実運用想定】

オンプレミス環境からの移行では、ユーザーにストレージアカウント名を意識させず、`\\corp.contoso.com\shares\sales` のような統一された UNC パスでアクセスさせる構成が一般的に使われます。

### 1. DFS 役割のインストール (vm-dc01 で実行)

`vm-dc01` 上の PowerShell で DFS ネームスペース機能を有効化します。

```powershell
Install-WindowsFeature -Name FS-DFS-Namespace -IncludeManagementTools
```

### 2. DFS ルートおよびフォルダターゲットの設定 (vm-dc01 で実行)

`New-DfsnRoot` の `-TargetPath` には、あらかじめ存在する SMB 共有を指定する必要があります。`\\vm-dc01\shares` が存在しない状態で実行すると、`Root target share wasn't found` エラーになります。

まず、`vm-dc01` 上で DFS 名前空間ルート用のフォルダーと SMB 共有を作成します。

```powershell
New-Item -Path 'C:\DFSRoots\shares' -ItemType Directory -Force

New-SmbShare -Name 'shares' -Path 'C:\DFSRoots\shares' -FullAccess 'CORP\Domain Admins' -ChangeAccess 'CORP\Domain Users'
```

共有の存在を確認します。

```powershell
Get-SmbShare -Name 'shares'
Test-Path '\\vm-dc01\shares'
```

`Test-Path` が `True` になったら、DFS 名前空間を作成します。

```powershell
# DFS ルート（ネームスペース）の作成: \\corp.contoso.com\shares
New-DfsnRoot -Path "\\corp.contoso.com\shares" -Type Domainv2 -TargetPath "\\vm-dc01\shares"

# フォルダと Azure Files ターゲットの紐付け
New-DfsnFolder -Path "\\corp.contoso.com\shares\sales" -TargetPath "\\<ストレージアカウント名>.file.core.windows.net\file-share-sales"
```

`\\vm-dc01\shares` は DFS 名前空間ルートを保持するための共有です。Azure Files の実データ共有は、`New-DfsnFolder` でフォルダーターゲットとして登録します。

### 3. DFS UNC パス経由のマウント確認 (vm-client01 上で実行)

クライアント VM から、DFS-N の UNC パスを指定してマウントを試行します。

```powershell
net use Z: \\corp.contoso.com\shares\sales
```

DFS ルート経由でリダイレクトされ、Azure Files の Private Endpoint へ閉域接続・ドメイン認証でアクセスできることを確認します。

---

## Step 6: Microsoft Entra Connect 連携とアクセス制御・ライフサイクル同期検証【実運用想定】

オンプレミス AD DS のユーザーやセキュリティグループを Microsoft Entra Connect により Microsoft Entra ID (旧 Azure AD) に同期し、同期された Entra グループ単位で Azure Files の共有レベル IAM ロール（RBAC）を割り当ててアクセス制御を行います。また、AD 側での属性変更・メンバーシップ変更・アカウント無効化・削除が Entra ID 側へ自動追従するライフサイクル挙動を検証します。

```
[ オンプレミス AD DS (vm-dc01) ]
  ├─ ユーザー / セキュリティグループ作成・変更・削除（正のソース / マスター）
  │
  ▼ (Entra Connect 定期同期 / Delta Sync)
[ Microsoft Entra ID ]
  ├─ 同期済みユーザー (On-premises sync enabled)
  └─ 同期済みセキュリティグループ (Security Group)
        │
        ▼ (Azure RBAC / IAM ロール割り当て)
[ Azure Files 共有 (file-share-sales) ]
  ├─ 共有レベル権限 (Storage File Data SMB Share Contributor / Reader)
  └─ ディレクトリ・ファイルレベル権限 (NTFS ACL)
```

> **[!WARNING] テナント管理者権限および本番テナント利用時の重要注意事項**
>
> 1. **必要な権限**: Entra Connect の初期セットアップには、Microsoft Entra ID 側の「グローバル管理者 (Global Administrator)」または「ハイブリッド ID 管理者 (Hybrid Identity Administrator)」ロールが必要です（Azure サブスクリプションの所有者/共同作成者ロールだけではセットアップできません）。
> 2. **本番テナントへの同期リスク**: 本番・会社テナントで安易にセットアップすると、検証環境のテストユーザーやグループが本番 Entra ID にそのまま流し込まれたり、既存 UPN と競合する恐れがあります。
> 3. **推奨検証方式**:
>    - 推奨 A（専用検証テナントの利用）: Microsoft Entra 管理センターで無償の検証用テナントを新規作成し、そのテナントの管理者として `vm-dc01` と同期させる（本番環境に一切影響を与えません）。
>    - 推奨 B（OU フィルタリング）: 既存テナントを使う場合は、管理者の許可のもと、カスタム設定で検証用 OU（例: `OU=AzureFiles_Test`）配下のみを同期対象に限定する。

### 1. Microsoft Entra Connect のダウンロードとインストール手順

Microsoft の仕様変更により、従来の汎用ダウンロードセンターの直リンク（旧 URL）は廃止されています。現在は Microsoft Entra 管理センターから直接インストーラーを入手します。

**① インストーラー (`AzureADConnect.msi`) の入手方法**

以下のいずれかの方法でインストーラーを入手します。

- **方法 A: 手元の作業 PC でダウンロードして `vm-dc01` に配置（推奨）**
  1. 作業端末のブラウザで Microsoft Entra 管理センター (entra.microsoft.com) にアクセスし、管理者アカウントでサインインします。
  2. 左メニュー「ID」（または「ハイブリッド管理」）→「Microsoft Entra Connect」→「接続同期 (Connect sync)」を開きます。
  3. 「Microsoft Entra Connect のダウンロード」をクリックして `AzureADConnect.msi` を保存します。
  4. Bastion または RDP 経由で `vm-dc01` の `C:\` ドライブ等にコピーします。

- **方法 B: `vm-dc01` 内の Edge ブラウザから直接ダウンロード**
  1. `vm-dc01` の Microsoft Edge を起動し、https://entra.microsoft.com にアクセスしてサインインします。
  2. 同様に「接続同期」画面から `AzureADConnect.msi` をダウンロードします。

**② 初期セットアップウィザードの実行 (vm-dc01 で実行)**

`AzureADConnect.msi` を起動（または管理者 PowerShell で `Start-Process msiexec.exe -ArgumentList "/i C:\AzureADConnect.msi" -Wait`）します。

1. ようこそ: 「ライセンス条項とプライバシーに関する声明に同意する」にチェック →「続行」。
2. 簡単設定: 「簡単設定を使う (Use express settings)」を選択（OU 単位で同期を限定したい場合は「カスタマイズ」を選択）。
3. Microsoft Entra に接続: Entra ID の管理者アカウント（ハイブリッド ID 管理者またはグローバル管理者）を入力。
4. AD DS に接続: オンプレミス AD のドメイン管理者（例: `corp\Administrator`）とパスワードを入力。
5. Azure AD サインインの構成: ドメインがローカルドメイン（`corp.contoso.com`）の場合、「すべての UPN サフィックスを検証済みドメインと一致させずに続行する」にチェックを入れて「次へ」。
6. 構成: 「構成が完了したときに同期プロセスを開始する」にチェックを入れて「インストール」をクリック。

インストール完了後、自動的に初回の完全同期(Full Sync)が開始されます。

### 2. AD DS 側でテスト用グループとユーザーを作成 (vm-dc01 で実行)

管理者 PowerShell（または `dsa.msc` の GUI）で、検証用のユーザーおよびセキュリティグループを作成し、所属させます。

```powershell
# 1. 検証用セキュリティグループの作成 (Global / Security)
New-ADGroup -Name "SG_Files_Sales_RW" -GroupScope Global -GroupCategory Security -Description "Azure Files 営業部 読み書きグループ"
New-ADGroup -Name "SG_Files_Sales_RO" -GroupScope Global -GroupCategory Security -Description "Azure Files 営業部 読み取り専用グループ"

# 2. 検証用テストユーザーの作成
$secPassword = ConvertTo-SecureString "P@ssw0rd2026!Sync" -AsPlainText -Force

New-ADUser -Name "SyncUser01" -GivenName "Sync" -Surname "User01" `
    -SamAccountName "SyncUser01" -UserPrincipalName "SyncUser01@corp.contoso.com" `
    -AccountPassword $secPassword -Enabled $true -ChangePasswordAtLogon $false

New-ADUser -Name "SyncUser02" -GivenName "Sync" -Surname "User02" `
    -SamAccountName "SyncUser02" -UserPrincipalName "SyncUser02@corp.contoso.com" `
    -AccountPassword $secPassword -Enabled $true -ChangePasswordAtLogon $false

# 3. ユーザーをグループに追加
Add-ADGroupMember -Identity "SG_Files_Sales_RW" -Members "SyncUser01"
Add-ADGroupMember -Identity "SG_Files_Sales_RO" -Members "SyncUser02"

# 4. 所属確認
Get-ADGroupMember -Identity "SG_Files_Sales_RW" | Select-Object Name,SamAccountName
Get-ADGroupMember -Identity "SG_Files_Sales_RO" | Select-Object Name,SamAccountName
```

> GUI で操作する場合: `vm-dc01` で `dsa.msc`（Active Directory ユーザーとコンピューター）を起動し、Users OU 配下にユーザー・グループを作成してメンバー追加を行っても同等の設定が可能です。

### 3. Entra Connect 同期を実行して Entra ID 側を確認

手動で差分同期(Delta Sync)を実行して変更をクラウド側へ即座に反映します。

```powershell
# vm-dc01 の管理者 PowerShell で実行
Start-ADSyncSyncCycle -PolicyType Delta
```

同期状態の確認項目:

- Microsoft Entra 管理センター (entra.microsoft.com):
  - ユーザー (Users): `SyncUser01`, `SyncUser02` が一覧に表示され、「オンプレミスの同期が有効 (On-premises sync enabled)」が はい (Yes) になっていること。
  - グループ (Groups): `SG_Files_Sales_RW`, `SG_Files_Sales_RO` が表示され、メンバーに該当ユーザーが含まれていること。
- Synchronization Service Manager (GUI):
  - `vm-dc01` のスタートメニューから「Synchronization Service」を起動すると、同期プロセスの結果や追加されたオブジェクト数が GUI で確認できます。

### 4. 同期された Entra グループへ Azure Files の IAM ロールを割り当て

Azure Portal または Azure CLI を使い、同期された Entra グループに対して Azure Files の共有レベルアクセス権(RBAC)を付与します。

```bash
# Azure CLI によるロール割り当て例
# 1. 同期された Entra グループの Object ID を取得
GROUP_RW_ID=$(az ad group show --group "SG_Files_Sales_RW" --query id -o tsv)
GROUP_RO_ID=$(az ad group show --group "SG_Files_Sales_RO" --query id -o tsv)

# 2. ストレージアカウントのリソース ID を取得
STORAGE_ID=$(az storage account show --name "<ストレージアカウント名>" --resource-group "rg-azurefiles-adds-demo" --query id -o tsv)

# 3. 共有レベル権限（RBAC）を付与
# RWグループ：記憶域ファイル データの SMB 共有の共同作成者
az role assignment create \
  --role "Storage File Data SMB Share Contributor" \
  --assignee-object-id "$GROUP_RW_ID" \
  --assignee-principal-type "Group" \
  --scope "$STORAGE_ID"

# ROグループ：記憶域ファイル データの SMB 共有の閲覧者
az role assignment create \
  --role "Storage File Data SMB Share Reader" \
  --assignee-object-id "$GROUP_RO_ID" \
  --assignee-principal-type "Group" \
  --scope "$STORAGE_ID"
```

### 5. クライアント VM からのアクセス検証 (vm-client01 で実行)

1. `vm-client01` 上でテストユーザーに RDP ログオン権限を付与します（ローカル管理者で実行）。

```powershell
Add-LocalGroupMember -SID 'S-1-5-32-555' -Member 'CORP\SyncUser01'
Add-LocalGroupMember -SID 'S-1-5-32-555' -Member 'CORP\SyncUser02'
```

2. `SyncUser01` でログオンし、ファイルの作成・更新・削除ができることを確認します。
3. `SyncUser02` でログオンし、ファイルの読み取りは可能だが新規書き込みが拒否されることを確認します。

### 6. AD 側の編集・変更・削除と Entra ID への自動同期（ライフサイクル）検証

AD DS 側で各種変更を行った際、Entra ID 側のオブジェクトに自動反映（追従）される挙動を検証します。

**① ユーザー属性の変更 (Display Name / 部署等の更新)**

```powershell
# vm-dc01 上で実行
Set-ADUser -Identity "SyncUser01" -DisplayName "Sync User 01 (Tokyo)" -Department "Sales"

# 同期実行
Start-ADSyncSyncCycle -PolicyType Delta
```

- 期待される結果: Entra 管理センターの `SyncUser01` の表示名および部署属性が更新される。

**② グループメンバーシップの変更 (追加・除外)**

```powershell
# vm-dc01 上で実行: SyncUser01 を ROグループにも追加し、RWグループから除外
Add-ADGroupMember -Identity "SG_Files_Sales_RO" -Members "SyncUser01"
Remove-ADGroupMember -Identity "SG_Files_Sales_RW" -Members "SyncUser01" -Confirm:$false

# 同期実行
Start-ADSyncSyncCycle -PolicyType Delta
```

- 期待される結果: Entra ID 側の `SG_Files_Sales_RO` に `SyncUser01` が追加され、`SG_Files_Sales_RW` から除外される。

**③ ユーザーアカウントの無効化 (Disable)**

```powershell
# vm-dc01 上で実行
Disable-ADAccount -Identity "SyncUser02"

# 同期実行
Start-ADSyncSyncCycle -PolicyType Delta
```

- 期待される結果: Entra ID 側で `SyncUser02` の「アカウントが有効 (Account enabled)」が いいえ (false) に変更され、クラウド認証およびサインインが遮断される。

**④ ユーザー・グループの削除 (Delete)**

```powershell
# vm-dc01 上で実行
Remove-ADUser -Identity "SyncUser02" -Confirm:$false
Remove-ADGroup -Identity "SG_Files_Sales_RO" -Confirm:$false

# 同期実行
Start-ADSyncSyncCycle -PolicyType Delta
```

- 期待される結果: Entra ID 側でも該当ユーザーおよびグループが削除され、Entra 管理センターの「削除済みユーザー (Deleted users)」および「削除済みグループ (Deleted groups)」に移動（一時削除状態）する。

---

## 7. 検証時の重要ポイント & トラブルシューティング

- **AD DS がマスター（正のソース）**: 同期されたユーザーやグループの属性編集・メンバー変更は Entra 管理センター側からは直接編集できません（「オンプレミスから同期されているため変更不可」エラーとなります）。必ず AD DS 側で変更して同期させます。
- **共有レベル IAM と NTFS ACL の違い**:
  - Azure Files の IAM ロール（`Storage File Data SMB Share Contributor` 等）は「共有全体に対するアクセスゲート（共有レベル権限）」です。
  - 共有内部の特定フォルダーやファイルへの細かいアクセス制御を行う場合は、Windows クライアントからフォルダーのプロパティ→セキュリティタブ（または `icacls` コマンド）で AD DS セキュリティグループに対する NTFS ACL を構成してください。
- **同期サイクル**: Entra Connect の既定の同期スケジュールは 30分間隔 です。検証時は `Start-ADSyncSyncCycle -PolicyType Delta` を実行することで即座に差分を反映できます。

#!/bin/bash
set -e
# 必須環境変数: CHALLENGE_STORAGE_ACCOUNT, AZURE_CLIENT_ID
: "${CHALLENGE_STORAGE_ACCOUNT:?環境変数 CHALLENGE_STORAGE_ACCOUNT が未設定}"
: "${AZURE_CLIENT_ID:?環境変数 AZURE_CLIENT_ID が未設定}"

# az CLIはマネージドIDを自動では使わないため、明示的にログインする
az login --identity --client-id "$AZURE_CLIENT_ID" >/dev/null

CONTAINER='$web'
BLOB_PATH=".well-known/acme-challenge/${CERTBOT_TOKEN}"

echo -n "${CERTBOT_VALIDATION}" > /tmp/challenge_response

az storage blob upload \
  --account-name "$CHALLENGE_STORAGE_ACCOUNT" \
  --container-name "$CONTAINER" \
  --name "$BLOB_PATH" \
  --file /tmp/challenge_response \
  --auth-mode login \
  --overwrite

# 反映待ち(結果整合性があるので即座に見えないことがある)
sleep 5

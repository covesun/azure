#!/bin/bash
: "${CHALLENGE_STORAGE_ACCOUNT:?環境変数 CHALLENGE_STORAGE_ACCOUNT が未設定}"
: "${AZURE_CLIENT_ID:?環境変数 AZURE_CLIENT_ID が未設定}"

# az CLIはマネージドIDを自動では使わないため、明示的にログインする(既にログイン済みでも無害)
az login --identity --client-id "$AZURE_CLIENT_ID" >/dev/null 2>&1 || true

CONTAINER='$web'
BLOB_PATH=".well-known/acme-challenge/${CERTBOT_TOKEN}"

az storage blob delete \
  --account-name "$CHALLENGE_STORAGE_ACCOUNT" \
  --container-name "$CONTAINER" \
  --name "$BLOB_PATH" \
  --auth-mode login || true

rm -f /tmp/challenge_response

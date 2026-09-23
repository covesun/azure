#!/bin/bash
set -e
# 必須環境変数: ACME_DOMAIN, KEYVAULT_NAME, KEYVAULT_CERT_NAME, AZURE_CLIENT_ID
: "${ACME_DOMAIN:?環境変数 ACME_DOMAIN が未設定}"
: "${KEYVAULT_NAME:?環境変数 KEYVAULT_NAME が未設定}"
: "${KEYVAULT_CERT_NAME:?環境変数 KEYVAULT_CERT_NAME が未設定}"
: "${AZURE_CLIENT_ID:?環境変数 AZURE_CLIENT_ID が未設定}"

# az CLIはマネージドIDを自動では使わないため、明示的にログインする(既にログイン済みでも無害)
az login --identity --client-id "$AZURE_CLIENT_ID" >/dev/null 2>&1 || true

LIVE_DIR="/etc/letsencrypt/live/${ACME_DOMAIN}"
PFX_PATH="/tmp/${KEYVAULT_CERT_NAME}.pfx"

openssl pkcs12 -export \
  -out "$PFX_PATH" \
  -inkey "${LIVE_DIR}/privkey.pem" \
  -in "${LIVE_DIR}/cert.pem" \
  -certfile "${LIVE_DIR}/chain.pem" \
  -passout pass:

az keyvault certificate import \
  --vault-name "$KEYVAULT_NAME" \
  --name "$KEYVAULT_CERT_NAME" \
  --file "$PFX_PATH"

rm -f "$PFX_PATH"

python3 /home/site/wwwroot/notify.py --status success --domain "$ACME_DOMAIN"

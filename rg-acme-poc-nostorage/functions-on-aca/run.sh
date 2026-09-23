#!/bin/bash
set -o pipefail
# 必須環境変数: ACME_DOMAIN, ACME_EMAIL, ACME_SERVER
#   ACME_SERVER 例: https://acme-staging-v02.api.letsencrypt.org/directory (検証時)
#                   https://acme-v02.api.letsencrypt.org/directory (本番Let's Encrypt。将来的にGMOグローバルサインのACMEディレクトリURLに差し替え予定)
: "${ACME_DOMAIN:?環境変数 ACME_DOMAIN が未設定}"
: "${ACME_EMAIL:?環境変数 ACME_EMAIL が未設定}"
: "${ACME_SERVER:?環境変数 ACME_SERVER が未設定}"

HOOKS_DIR="/home/site/wwwroot/hooks"
LOGFILE="/tmp/certbot_run_$(date +%s).log"

# 診断用: マネージドID関連の環境変数が実際に注入されてるか(値は出さず名前だけ)
echo "--- identity-related env var names present ---" >> "$LOGFILE"
env | cut -d= -f1 | grep -iE "identity|msi|azure_client" >> "$LOGFILE" || echo "(none found)" >> "$LOGFILE"
echo "-----------------------------------------------" >> "$LOGFILE"

certbot certonly \
  --manual \
  --preferred-challenges http \
  --manual-auth-hook "${HOOKS_DIR}/auth-hook.sh" \
  --manual-cleanup-hook "${HOOKS_DIR}/cleanup-hook.sh" \
  --deploy-hook "${HOOKS_DIR}/deploy-hook.sh" \
  -d "$ACME_DOMAIN" \
  --email "$ACME_EMAIL" --agree-tos --non-interactive \
  --server "$ACME_SERVER" \
  > "$LOGFILE" 2>&1

RESULT=$?
if [ $RESULT -ne 0 ]; then
  python3 /home/site/wwwroot/notify.py --status failure --log "$LOGFILE"
fi

cat "$LOGFILE"
exit $RESULT

#!/bin/bash
set -e
# blob版と違い、Storageへは一切触らない。チャレンジ応答はこのコンテナの
# ローカルディスク(/tmp/acme-challenge/)に置くだけで、Azure APIコールが不要になった。
# 配信は同じコンテナ内のFunction App(AcmeChallenge、function_app.py)が行う。
# ※Function Appはminロ/maxReplicas=1固定が前提(同一インスタンスでauth-hookと
#   AcmeChallenge関数のファイルシステムを共有する必要があるため)。

CHALLENGE_DIR="/tmp/acme-challenge"
mkdir -p "$CHALLENGE_DIR"

echo -n "${CERTBOT_VALIDATION}" > "${CHALLENGE_DIR}/${CERTBOT_TOKEN}"

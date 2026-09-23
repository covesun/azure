#!/bin/bash
# blob版と違い、Storageへは一切触らない。ローカルに置いたチャレンジ応答ファイルを消すだけ。

CHALLENGE_DIR="/tmp/acme-challenge"
rm -f "${CHALLENGE_DIR}/${CERTBOT_TOKEN}"

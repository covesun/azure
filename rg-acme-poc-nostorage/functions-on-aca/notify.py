import argparse
import os

from azure.identity import DefaultAzureCredential
from azure.communication.email import EmailClient

# 必須環境変数: ACS_ENDPOINT, ACS_SENDER_ADDRESS, NOTIFY_TO_ADDRESS
# ユーザー割り当てマネージドIDを使う場合は AZURE_CLIENT_ID も設定しておくこと


def send(status: str, detail: str) -> None:
    acs_endpoint = os.environ["ACS_ENDPOINT"]
    sender_address = os.environ["ACS_SENDER_ADDRESS"]
    to_address = os.environ["NOTIFY_TO_ADDRESS"]

    # Container Apps上ではDefaultAzureCredential()任せだとユーザー割り当てIDを
    # 正しく選べないことがあるため、AZURE_CLIENT_IDを明示的に渡す
    client_id = os.environ.get("AZURE_CLIENT_ID")
    credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    client = EmailClient(acs_endpoint, credential)

    subject = f"[ACME PoC] 証明書更新: {'成功' if status == 'success' else '失敗'}"
    message = {
        "senderAddress": sender_address,
        "recipients": {"to": [{"address": to_address}]},
        "content": {"subject": subject, "plainText": detail},
    }
    poller = client.begin_send(message)
    poller.result()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True, choices=["success", "failure"])
    parser.add_argument("--domain", default="")
    parser.add_argument("--log", default="")
    args = parser.parse_args()

    if args.status == "success":
        detail = f"証明書の更新に成功しました: {args.domain}"
    else:
        with open(args.log, "r", encoding="utf-8", errors="replace") as f:
            detail = f.read()[-2000:]

    send(args.status, detail)

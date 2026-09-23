import logging
import os
import subprocess

import azure.functions as func

app = func.FunctionApp()


# スケジュール・起動時即時実行の可否は環境変数で制御する。
# ACA環境がinternal(VNet内限定)のため、Portal「コード+テスト」やVNet外からのcurlでは
# 手動実行できない(default hostnameがVNet内部からしか名前解決できない)。
# テスト時は CERTBOT_RUN_ON_STARTUP=true を設定して stop/start すれば、
# 外部からのHTTP到達性なしにコンテナ起動時に即時実行される(Timerトリガーの発火自体は
# ACAのスケールコントローラーが内部的に行うため、ingressの有無とは無関係)。
#
# 環境変数:
#   CERTBOT_SCHEDULE        : NCRONTAB形式のスケジュール。未設定時は毎日AM3時(UTC)
#   CERTBOT_RUN_ON_STARTUP  : "true"でコンテナ起動時に即時実行(テスト用途)。既定は"false"
CERTBOT_SCHEDULE_DEFAULT = "0 0 3 * * *"

# blob版と違い、チャレンジ応答はStorageを介さずこのコンテナのローカルディスクに直接置く。
# auth-hook.sh が書き込み、AcmeChallenge関数(下)がそのまま読んで返す。
# 同一コンテナインスタンス内で完結させる必要があるため、Function Appは
# minReplicas=maxReplicas=1固定が前提(functions-on-aca/README.md参照)。
CHALLENGE_DIR = "/tmp/acme-challenge"


@app.timer_trigger(
    schedule="%CERTBOT_SCHEDULE%",
    arg_name="mytimer",
    run_on_startup=os.environ.get("CERTBOT_RUN_ON_STARTUP", "false").lower() == "true",
    use_monitor=True,
)
def CertbotRenew(mytimer: func.TimerRequest) -> None:
    logging.info("certbot renewal run: start")

    result = subprocess.run(
        ["/home/site/wwwroot/run.sh"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    logging.info("certbot renewal run: stdout=%s", result.stdout)
    if result.returncode != 0:
        logging.error("certbot renewal run: FAILED (exit=%s) stderr=%s", result.returncode, result.stderr)
    else:
        logging.info("certbot renewal run: succeeded")


@app.route(route=".well-known/acme-challenge/{token}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def AcmeChallenge(req: func.HttpRequest) -> func.HttpResponse:
    # CA(Let's Encrypt)からAGW:80経由で匿名アクセスされるエンドポイント。
    # function keyを要求すると検証が失敗するため auth_level は必ず ANONYMOUS。
    token = req.route_params.get("token", "")
    # パストラバーサル対策(念のため。tokenはACMEサーバーが発行する英数字のはず)
    if not token or "/" in token or ".." in token:
        return func.HttpResponse(status_code=404)

    path = os.path.join(CHALLENGE_DIR, token)
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except FileNotFoundError:
        logging.warning("acme challenge token not found: %s", token)
        return func.HttpResponse(status_code=404)

    return func.HttpResponse(body, status_code=200, mimetype="text/plain")


@app.route(route="healthz", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def Healthz(req: func.HttpRequest) -> func.HttpResponse:
    # AGWのカスタムヘルスプローブ用(02a-appgateway-bootstrap.jsonのprobe-challengeが見に来る)
    return func.HttpResponse("ok", status_code=200, mimetype="text/plain")

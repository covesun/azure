import logging
import os
import subprocess

import azure.functions as func

app = func.FunctionApp()


# スケジュール・起動時即時実行の可否は環境変数で制御する。
# ACA環境がinternal(VNet内限定)のため、Portal「コード+テスト」やVNet外からのcurlでは
# 手動実行できない(default hostnameがVNet内部からしか名前解決できない)。
# テスト時は CERTBOT_RUN_ON_STARTUP=true を設定して `az functionapp restart` すれば、
# 外部からのHTTP到達性なしにコンテナ起動時に即時実行される(Timerトリガーの発火自体は
# ACAのスケールコントローラーが内部的に行うため、ingressの有無とは無関係)。
#
# 環境変数:
#   CERTBOT_SCHEDULE        : NCRONTAB形式のスケジュール。未設定時は毎日AM3時(UTC)
#   CERTBOT_RUN_ON_STARTUP  : "true"でコンテナ起動時に即時実行(テスト用途)。既定は"false"
CERTBOT_SCHEDULE_DEFAULT = "0 0 3 * * *"


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

FROM mcr.microsoft.com/azure-functions/python:4-python3.11

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

# Azure CLI (hookスクリプトからstorage/keyvault操作に使う) + certbot + openssl(PFX変換用)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg lsb-release openssl \
    && curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt \
    && pip install --no-cache-dir certbot

COPY . /home/site/wwwroot
RUN chmod +x /home/site/wwwroot/hooks/*.sh /home/site/wwwroot/run.sh

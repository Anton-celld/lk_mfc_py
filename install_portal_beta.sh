#!/usr/bin/env bash
# Только Python-версия МФЦ для ЛК студента. Не устанавливает PHP-код и не трогает старый раздел.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Нужен root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/opt/lk_mfc_beta
if [ "$SRC" != "$DST" ]; then
  mkdir -p "$DST"
  tar -C "$SRC" --exclude=data --exclude=config.json --exclude=portal_config.json --exclude=.venv --exclude=__pycache__ -cf - . | tar -C "$DST" -xf -
fi
id mfcbeta >/dev/null 2>&1 || useradd --system --home "$DST" --shell /usr/sbin/nologin mfcbeta
[ -f "$DST/portal_config.json" ] || cp "$DST/portal_config.example.json" "$DST/portal_config.json"
[ -f /etc/lk_mfc_beta.env ] || { echo "Создайте /etc/lk_mfc_beta.env с MFC_PORTAL_SSO_SECRET до установки (см. README_PORTAL_PYTHON.md)"; exit 1; }
# Содержимое /etc/lk_mfc_beta.env — файл администратора, без вывода секретов в журнал.
set -a
# shellcheck disable=SC1091
source /etc/lk_mfc_beta.env
set +a
python3 - "$DST/portal_config.json" <<'PY2'
import json, os, sys
c=json.load(open(sys.argv[1],encoding="utf-8"))
assert c.get("public_url", "").startswith("https://") and "example" not in c["public_url"], "Укажите реальный HTTPS-адрес портала"
onec=c.get("onec",{})
transport=os.environ.get("MFC_ONEC_TRANSPORT") or onec.get("transport", "http")
assert transport in ("http","soap"), "Некорректный MFC_ONEC_TRANSPORT"
if transport == "soap":
    assert os.environ.get("MFC_ONEC_WSDL") or onec.get("wsdl"), "Укажите MFC_ONEC_WSDL"
else:
    assert os.environ.get("MFC_ONEC_BASE_URL") or onec.get("base_url"), "Укажите HTTP-адрес сервиса 1С"
PY2'
import json,sys
c=json.load(open(sys.argv[1],encoding="utf-8"))
assert c.get("public_url","").startswith("https://") and "example" not in c["public_url"], "Укажите реальный HTTPS-адрес портала"
assert c.get("onec",{}).get("base_url"), "Настройте адрес интеграционного HTTP-сервиса 1С (см. ограничения в README)"
PY2
apt-get install -y python3-venv python3-pip >/dev/null
[ -x "$DST/.venv/bin/python" ] || python3 -m venv "$DST/.venv"
if [ "${MFC_ONEC_TRANSPORT:-}" = "soap" ] || [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("onec",{}).get("transport", "http"))' "$DST/portal_config.json")" = "soap" ]; then
  "$DST/.venv/bin/pip" install -r "$DST/requirements-soap.txt"
else
  "$DST/.venv/bin/pip" install -r "$DST/requirements.txt"
fi
mkdir -p "$DST/data"
chown -R mfcbeta:mfcbeta "$DST"
chmod 700 "$DST/data"
chmod 600 "$DST/portal_config.json" /etc/lk_mfc_beta.env
cat > /etc/systemd/system/lk-mfc-beta.service <<UNIT
[Unit]
Description=Студенческие услуги МФЦ (Бета) — Python
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mfcbeta
WorkingDirectory=$DST
EnvironmentFile=/etc/lk_mfc_beta.env
ExecStart=$DST/.venv/bin/python portal_run.py --config $DST/portal_config.json
Restart=on-failure
RestartSec=4
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DST/data
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now lk-mfc-beta.service
systemctl restart lk-mfc-beta.service
systemctl --no-pager status lk-mfc-beta.service

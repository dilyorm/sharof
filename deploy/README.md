# Deploy sharof on oraclewps

## 1. Postgres
```bash
sudo apt update && sudo apt install -y postgresql
sudo -u postgres psql <<'SQL'
CREATE USER sharof WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE sharof OWNER sharof;
SQL
```

## 2. Code
```bash
sudo mkdir -p /opt/sharof && sudo chown $USER /opt/sharof
git clone https://github.com/dilyorm/sharof.git /opt/sharof
cd /opt/sharof
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: TELEGRAM_TOKEN, OPENROUTER_API_KEY, DATABASE_URL (with the password above)
```

## 3. Schema
Schema auto-creates on first start (init_schema). To load manually:
```bash
psql "$DATABASE_URL" -f schema.sql
```

## 4. systemd
```bash
sudo useradd -r -s /usr/sbin/nologin sharof || true
sudo chown -R sharof /opt/sharof
sudo cp deploy/sharof.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sharof
sudo systemctl status sharof
journalctl -u sharof -f
```

## 5. BotFather — REQUIRED
Disable group privacy so the bot reads all group messages:
`/setprivacy` → select bot → **Disable**. Then re-add the bot to groups.

## Update
```bash
cd /opt/sharof && git pull && .venv/bin/pip install -r requirements.txt
sudo systemctl restart sharof
```

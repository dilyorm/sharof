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

## 5. Take the token back from miki-bot

Sharof and `miki-bot` share the `@sharof_gapir_bot` token, and Telegram allows
one poller per token. Sharof now does everything miki-bot did (shell + files on
the server) plus PC control, so retire it:

```bash
sudo systemctl disable --now miki-bot
```

## 6. Remote control of the home PC

The PC dials out; the server never dials in. On the **PC**, miki spawns
`ssh -N -R 9099:127.0.0.1:9099 user@server`, which publishes miki's local HTTP
port on this server's `127.0.0.1:9099`. Sharof calls it as `PC_URL`.

On the PC:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/miki_tunnel -N ""      # no passphrase: it runs unattended
```

On the server, add the public key to `~/.ssh/authorized_keys` **with the tunnel
restriction** — this key can then only hold that one forward, never a shell:

```
restrict,port-forwarding,permitlisten="127.0.0.1:9099" ssh-ed25519 AAAA... miki-tunnel
```

Then in miki's `.env` on the PC: `MIKI_SECRET`, `TUNNEL_HOST`, `TUNNEL_USER`,
`TUNNEL_KEY`. In sharof's `.env` here: `OWNER_IDS` (your Telegram user id),
`PC_SECRET` (= `MIKI_SECRET`).

Check the tunnel from the server:

```bash
curl -H "X-Miki-Secret: $PC_SECRET" http://127.0.0.1:9099/health   # {"ok":true}
```

**Security:** the tools are remote code execution on both machines. `OWNER_IDS`
is the wall — only those Telegram user ids get tools, and only in a private
chat. `ALLOW_SHELL=false` disables shell everywhere without touching anything
else.

## 7. BotFather — REQUIRED
Disable group privacy so the bot reads all group messages:
`/setprivacy` → select bot → **Disable**. Then re-add the bot to groups.

## Update
```bash
cd /opt/sharof && git pull && .venv/bin/pip install -r requirements.txt
sudo systemctl restart sharof
```

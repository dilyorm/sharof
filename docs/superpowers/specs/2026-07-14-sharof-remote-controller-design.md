# Sharof as remote controller (server bot → home PC via miki)

Date: 2026-07-14
Status: approved, ready for plan

## Goal

Sharof (Telegram bot on the server) receives an instruction, decides on its own
whether it can answer, must run something on the server, or must drive the
owner's home PC — and in the last case talks to **miki**, the Electron app
already running on that PC.

## Current state

- **sharof** (`/opt/sharof`, systemd `sharof.service`, currently *stopped*):
  Python/aiogram, Postgres (`messages`, `chat_state`), OpenRouter chat +
  group-interject gate. No tools, no PC channel.
- **miki** (`projects/miki`): Electron desktop app on the owner's Windows box.
  Regex intent engine (`intents.js` + `actions.js`): open app, open URL, search,
  play music. Always running (tray).
- **miki server-bot** (`projects/miki/server-bot`, `/opt/miki-bot`, systemd
  `miki-bot.service`): standalone Node Telegram bot doing OpenRouter
  tool-calling (`run_shell`, `read_file`, `write_file`, `http_get`) **on the
  server**. It holds the `@sharof_gapir_bot` token, which is why
  `sharof.service` is stopped — Telegram allows one poller per token.

## Decisions

1. **One bot.** Sharof absorbs miki server-bot. `miki-bot.service` is stopped
   and disabled, `server-bot/` is deleted from the miki repo, and its tool logic
   is ported to Python inside sharof. Sharof keeps the `@sharof_gapir_bot` token.
2. **PC powers:** miki's existing intents **plus** arbitrary PowerShell / file
   access on the PC. Owner-locked.
3. **Transport:** reverse SSH tunnel initiated by the PC. No inbound port, no
   new firewall rule, no TLS to manage — reuses port 22 and an SSH key.
4. **Routing:** LLM tool-calling. The model decides PC vs server vs plain chat.
   Tools are offered **only** in a private chat with an owner user id.

## Architecture

```
Telegram (@sharof_gapir_bot)
   │  owner DM: "open youtube on my pc"
   ▼
sharof (oraclewps, systemd, Python)          tool-call loop, owner DM only
   ├── server_run / read_file / write_file ──► shell + files on the server
   └── pc_run / pc_intent ──► POST http://127.0.0.1:9099 ─┐
                                                          │  reverse SSH tunnel
   Home PC (Windows, miki always running)                 │  (port 22, existing key)
   ├── remote.js: HTTP server on 127.0.0.1:9099  ◄────────┘
   │     POST /run    → PowerShell, timeout, truncated output
   │     POST /intent → existing intents.js + actions.js
   │     GET  /health
   └── child process: ssh -N -R 9099:127.0.0.1:9099 user@oraclewps
         (ExitOnForwardFailure, ServerAliveInterval; respawn with backoff)
```

Non-owner DMs and all group chats keep today's behaviour exactly: chat replies,
`/summarize`, and the interject gate. No tools, no PC access.

## Components

### sharof/tools.py (new)

OpenAI-style tool schemas + an async executor.

| Tool | Runs where | Notes |
|---|---|---|
| `server_run(command, timeout_ms=15000)` | server | deny-list; disabled if `ALLOW_SHELL=false` |
| `read_file(path)` | server | UTF-8, truncated to 4000 chars |
| `write_file(path, content)` | server | overwrites |
| `pc_run(command, timeout_ms=15000)` | home PC | PowerShell via miki `/run` |
| `pc_intent(text)` | home PC | miki `/intent` — open app/URL/search/play |

- **Deny-list** (ported from `server-bot/tools.js`): `rm -rf /`, `mkfs`, fork
  bomb `:(){:|:&};:`, `shutdown`, `reboot`, `dd if=`, `> /dev/sd*`. Applied to
  `server_run` **and** `pc_run` (PC additions: `Format-Volume`, `Stop-Computer`,
  `Remove-Item` on a drive root). A guard, not a sandbox.
- **Output**: every tool returns a string, truncated at 4000 chars.
- **PC unreachable** (`httpx.ConnectError` / connection refused): return the
  string `"PC offline (tunnel down)"` rather than raising, so the model reports
  it to the user in chat.

### sharof/agent.py (new)

`handle(client, settings, chat_id, text, history) -> str`

- Messages: `[system(tool prompt), ...recent history, user text]`.
- Calls OpenRouter with `tools=` using `settings.openrouter_tool_model`,
  `max_tokens=512`, `temperature=0.3`.
- Loop: run every returned tool call, append `role=tool` results, re-call.
  **Cap 4 iterations**; on cap, return the last assistant text or a "gave up"
  line.
- Reuses `llm._post`-style retry/backoff on 429/5xx; raises `LLMError`, which
  `bot.py` already handles with the "busy, try again" reply.

### sharof/bot.py (changed)

In `on_text`, private chat branch:

```
if chat.type == "private" and user and user.id in settings.owner_ids:
    answer = await agent.handle(...)   # tools live here, and only here
else:
    await _reply_with_context(message)  # unchanged
```

Group branch untouched. Bot replies are still stored in Postgres, so PC actions
appear in history and the model has continuity.

### sharof/config.py (changed)

New settings:

- `OWNER_IDS` — comma-separated Telegram **user ids** (not chat ids). Required
  for tools; empty ⇒ tools are simply never offered.
- `PC_URL` — default `http://127.0.0.1:9099`.
- `PC_SECRET` — sent as `X-Miki-Secret`; must match miki's.
- `OPENROUTER_TOOL_MODEL` — default `deepseek/deepseek-chat-v3-0324` (the model
  miki-bot already used successfully for tool calls). Chat/summarize/judge stay
  on the free `openai/gpt-oss-120b:free`, which is not reliable at tool calling.
- `ALLOW_SHELL` — default `true`; `false` disables `server_run` and `pc_run`
  (intents still work).

### miki/remote.js (new, in projects/miki)

Node `http` server bound to `127.0.0.1:9099`:

- `POST /run` `{command, timeout_ms}` → `powershell -NoProfile -Command`, returns
  `{stdout, stderr, code}`, truncated.
- `POST /intent` `{text}` → existing `intents.js` + `actions.js`, returns the
  same reply string the chat window shows.
- `GET /health` → `{ok:true}`.
- Every request requires header `X-Miki-Secret` matching `MIKI_SECRET` from the
  root `.env`; otherwise 401. This is what stops anything else with access to
  the server's localhost from driving the PC.

### miki tunnel (in projects/miki/main.js)

On app ready, spawn:

```
ssh -N -T -R 9099:127.0.0.1:9099 <user>@<host> \
    -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3
```

Host/user/key path from `.env` (`TUNNEL_HOST`, `TUNNEL_USER`, `TUNNEL_KEY`).
On exit, respawn with exponential backoff (2s → 60s cap). Kill on app quit.

### miki cleanup

Delete `server-bot/`; update `README.md` (the Telegram bot now lives in the
sharof repo). On the server: `systemctl disable --now miki-bot`.

## Security

- **Owner lock** by Telegram `from_user.id`, checked in `bot.py` before the
  agent is ever called. This is the primary wall — the tools are remote code
  execution on two machines.
- **SSH key hardening**: the tunnel key's entry in the server's
  `~/.ssh/authorized_keys` is prefixed
  `restrict,port-forwarding,permitlisten="127.0.0.1:9099"` — the key cannot get
  a shell and cannot forward anything else.
- **Shared secret** on the miki HTTP port (`X-Miki-Secret`).
- **Deny-list** on both shells; `ALLOW_SHELL=false` as a kill switch.
- Tunnel listens on `127.0.0.1` only (default `sshd` behaviour, no
  `GatewayPorts`), so the PC port is never exposed to the internet.

## Error handling

| Failure | Behaviour |
|---|---|
| Tunnel down / miki not running | `pc_*` returns `"PC offline (tunnel down)"`; model tells the user |
| PC command times out | tool returns `"timed out after Ns"` |
| Bad secret | 401 → `"PC rejected the request (secret mismatch)"` |
| OpenRouter 429/5xx | existing retry/backoff, then the existing "busy" reply |
| Tool loop hits 4 iterations | return last assistant text, or "couldn't finish" |
| Deny-listed command | tool returns `"refused: destructive command"` |

## Testing

pytest (existing suite, httpx mocked — no network, no real shell):

- deny-list refuses each catastrophic pattern, allows normal commands;
- owner gate: tools offered for an owner's private chat only — not for another
  user's DM, not in groups;
- `pc_run` maps connection-refused to the "PC offline" string;
- agent loop stops at 4 iterations and returns a reply;
- config: `OWNER_IDS` parsing, defaults.

miki: one node test that boots `remote.js` with a stubbed action layer and
asserts `/health`, `/intent`, 401 on a wrong secret.

## Out of scope

- Streaming/long-running PC jobs (each tool call is request/response).
- Screenshots, file transfer to/from the PC, tray status for the tunnel.
- Multiple PCs. `PC_URL` is a single host.

# Taiga on Docker + Tailscale — runbook

Use this when (re)installing Taiga via this repo on a Mac, exposing it on your tailnet, or after the host’s Tailscale IP or hostname changes.

## What must be true

1. **Docker Compose runs on one specific machine** (e.g. MacBook or Mac mini). That machine is the only one that should publish port **9000** (see `docker-compose.yml`: `taiga-gateway` → `9000:80`).
2. **`TAIGA_DOMAIN` in `.env` is the public URL users type in the browser**, including port if non-default: `host:9000`. Taiga’s front, API, and websocket URLs are derived from it. Wrong host = broken login, API, or realtime.
3. **`localhost` is not a URL you give to other people.** `http://localhost:9000` always means “this computer.” Remote users need your **Tailscale IP** or **MagicDNS name** and port **9000**.

## Quick reference — `.env` (URLs)

```bash
TAIGA_SCHEME=http          # https only if you terminate TLS (proxy, Serve, etc.)
TAIGA_DOMAIN=<reachable-host>:9000
SUBPATH=""
WEBSOCKETS_SCHEME=ws       # wss if frontend is served over HTTPS
```

Replace `<reachable-host>` with:

- **Tailscale IP** of the Mac that runs Docker (e.g. `100.71.62.14`), or  
- **MagicDNS hostname** if enabled (e.g. `my-mac.tail12345.ts.net`), still with `:9000` unless you remap ports.

After **any** change to these lines:

```sh
cd /path/to/taiga-docker
docker compose -f docker-compose.yml up -d --force-recreate
```

## Find the right Tailscale identity (critical)

On **the machine where Docker runs**:

```sh
tailscale status
```

- Note the line for **this** machine (often marked “self” or obvious from hostname).
- Use **that** row’s `100.x.x.x` (or MagicDNS name) in `TAIGA_DOMAIN`, **not** another device (VM, Linux box, phone, etc.).

**Typical mistake:** `TAIGA_DOMAIN` pointed at a **different** tailnet node (e.g. `100.73.x.x` on `my-docker-tailscale`) while Taiga actually runs on the Mac (`100.71.x.x`). The wrong IP will not answer on port 9000, or the app will misconfigure URLs.

## Verify from the Docker host

```sh
docker compose -f docker-compose.yml ps
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/
curl -sS -o /dev/null -w '%{http_code}\n' http://$(tailscale ip -4):9000/
```

Expect `200` (or redirect) from localhost and from the host’s own Tailscale IP.

## Verify from another device on the tailnet

- Open `http://<that-mac-tailscale-ip>:9000` (or MagicDNS + `:9000`).
- If that fails: confirm Tailscale connected, check **macOS Firewall** for Docker/incoming **9000**, and Tailscale **ACLs** (default allow is usually enough).

## Moving the stack to another machine (e.g. Mac mini)

1. Install Docker and this repo on the **new** Mac; copy/adjust `.env` (do not blindly copy old `TAIGA_DOMAIN`).
2. Install Tailscale on the **new** Mac; join the same tailnet.
3. Run `tailscale status` **on the new Mac**; set `TAIGA_DOMAIN=<new-mac-tailscale-ip-or-dns>:9000`.
4. Start: `./launch-taiga.sh` (or `docker compose -f docker-compose.yml up -d`).
5. Run DB/migrate/createsuperuser as in the main README if this is a **fresh** data volume.
6. Tell users the **new** URL; old `100.x` will not point to the new Mac unless you reuse hostname/IP (you generally won’t).

## Platform note (Apple Silicon)

Images may log `linux/amd64` vs `arm64` warnings; they often still run under emulation. If something crashes only on ARM, note that in support requests.

## Obsolete Compose warning

`version:` at the top of `docker-compose.yml` may warn as obsolete; safe to ignore or remove in a future cleanup.

---

## Copy-paste prompt for the next debugging session

Use this (fill in brackets) in chat with a helper/AI:

```text
Taiga: taiga-docker repo, gateway on host port 9000.

Host: [Mac model], Docker Desktop, Tailscale installed.
Problem: [e.g. cannot open http://100.x.x.x:9000 / API errors / websocket errors]

I set in .env:
TAIGA_SCHEME=...
TAIGA_DOMAIN=...
WEBSOCKETS_SCHEME=...

Output of `tailscale status` on the Docker host (paste):
[paste]

Output of `docker compose -f docker-compose.yml ps` (paste):
[paste]

From the host: `curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/` returns [code].
I need TAIGA_DOMAIN and firewall/Tailscale checks aligned so tailnet clients can use [desired URL].
```

---

## Related — project docs

- Main install: `README.md` (`./launch-taiga.sh`, `./taiga-manage.sh createsuperuser`).
- Taiga URL semantics: README “Basic Configuration” / “URLs settings”.

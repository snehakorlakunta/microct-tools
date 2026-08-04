# Hosting the UI remotely (Vercel) against this local server

The dashboard UI can be served from the public internet — a Next.js build on
Vercel — while **all data stays on your machine**. The hosted page is only HTML,
CSS, and JavaScript. Once it loads, it runs *in your browser* and calls this
server directly at `http://localhost:8000`. Vercel's servers never see a dataset,
a mask, or the registry.

```
   ┌─────────────────┐   1. load page (HTML/JS only)   ┌──────────────────┐
   │  your browser   │ ──────────────────────────────► │  Vercel (static) │
   │                 │ ◄────────────────────────────── │                  │
   │                 │                                  └──────────────────┘
   │                 │   2. every API call + every
   │                 │      volume goes here instead
   │                 │ ──────────────────────────────► ┌──────────────────┐
   └─────────────────┘                                  │  localhost:8000  │
                                                        │  this server     │
                                                        │  + registry.db   │
                                                        │  + your datasets │
                                                        └──────────────────┘
```

Nothing about your data leaves the machine, and the large NIfTI volumes travel
over loopback rather than the internet — so the viewer stays fast.

---

## Setup

### 1. Configure this server

In `.env`:

```ini
# The exact origin of the hosted UI. Comma-separated, no trailing slash.
MICROCT_ALLOWED_ORIGINS=https://microctweb.vercel.app

# Shared secret the UI must send on every request.
MICROCT_API_TOKEN=<paste a generated token>
```

Generate a token with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Restart the server. It refuses to stay quiet about a misconfiguration: if
`ALLOWED_ORIGINS` names a non-localhost origin and no token is set, it emits a
warning at startup, because that combination leaves the API open to any website.

### 2. Configure the UI

Open the hosted page and use the **Connection** panel in the header:

- **API base URL** — `http://localhost:8000`
- **API token** — the token from `.env`

Both are stored in your browser's `localStorage` (`microct_api_base` and
`microct_api_token`), so this is a one-time step per browser. Nothing is baked
into the Vercel build, which means the same build works for every colleague
pointing at their own machine.

---

## Why a token is not optional

CORS is often misunderstood as an access control. It is not. It stops a
malicious page from *reading* this API's responses, but a "simple" cross-origin
request still **executes** — a drive-by `POST` could enqueue or delete something
even though the attacker never sees the reply.

Requiring `Authorization: Bearer <token>` closes that hole twice over. The
obvious way: no token, no service. The subtle and more important way: a custom
header makes every request "non-simple", which forces the browser to send a CORS
preflight *first*. The origin check therefore runs before anything can mutate
state, instead of after.

`allow_credentials` is deliberately **off**. Auth is a bearer token, never a
cookie, so the browser never attaches ambient credentials to a cross-origin
request and there is no CSRF surface to reason about.

`GET /api/health` is the one unauthenticated endpoint. It exists so the UI can
tell "server is down" apart from "server is up but my token is wrong", and it
deliberately reveals nothing but the app name, version, and whether auth is on:

```json
{"ok": true, "app": "microct-seg-lab", "version": "0.1.0", "auth_required": true}
```

---

## Private Network Access

A public HTTPS page reaching a loopback address is exactly the pattern browser
vendors have been tightening. Chrome requires the page to pass a **Private
Network Access** preflight carrying `Access-Control-Request-Private-Network:
true`, which the server must answer with `Access-Control-Allow-Private-Network:
true`.

This is handled by `allow_private_network=True` on the CORS middleware in
`main.py`. Do not try to add that response header with your own middleware:
Starlette rejects the private-network preflight *internally* with a 400 before
your middleware ever sees it, so the header gets attached to an already-failed
response and the browser still blocks the request. The flag is the only correct
fix.

### Browser support — read this before promising it to colleagues

| Browser | Status |
|---|---|
| Chrome / Edge | **Verified working.** Expect a one-time local-network permission prompt on newer versions. |
| Firefox | Expected to work — treats `localhost` as a trustworthy origin and does not implement PNA preflights. Not yet verified here. |
| Safari | **Likely blocked.** Safari does not implement PNA and is stricter about HTTPS pages reaching localhost. Use the local-serving mode below. |

---

## The fallback that always works: serve the same build locally

The UI is built as a **static export**, so the identical build can be served by
this server instead of by Vercel. Then everything is same-origin and none of the
above applies — no CORS, no PNA, no token, no browser caveats, and it works
fully offline from the USB bundle.

```bash
cd ../../microctweb
npm run build          # produces out/
# then point the server's web directory at that build, or copy out/ over
# src/microct_lab/web/
```

Treat Vercel hosting as the convenience path and local serving as the reliable
one. If a colleague's browser refuses the localhost call, this is the answer —
and it is also the answer for an air-gapped machine.

---

## Troubleshooting

**"Connected, but the token is missing or wrong" (HTTP 401)**
The token in the Connection panel does not match `MICROCT_API_TOKEN`. Note the
server must be restarted after changing `.env`.

**The UI says the server is unreachable, but it is running**
Check the origin matches *exactly* — scheme, host, and port, no trailing slash.
`https://microctweb.vercel.app` and `https://microctweb.vercel.app/` are the
same to you and different to the browser. Vercel preview deployments get their
own per-deployment hostnames, so add those too, or test against the production
alias only.

**Requests fail only in the browser, but `curl` works**
That is the signature of a CORS or PNA rejection rather than a server error —
`curl` does not enforce either. Open the browser devtools Network tab and look
at the failed `OPTIONS` preflight; its response body names what was disallowed.

**Images and volumes fail to load while JSON calls succeed**
Binary endpoints (`/thumbnail`, `/preview.png`, `*.nii.gz`) cannot carry an
`Authorization` header when used as a bare `<img src>` or handed to a viewer as
a URL. They must be fetched with the header and turned into blob URLs. The UI
routes all of these through a single helper for exactly this reason.

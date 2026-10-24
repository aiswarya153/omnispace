# OmniSpace 3D

FastAPI serves the Three.js editor and calls OpenAI on the server. The browser never receives the OpenAI API key. Generated layouts include validated furniture geometry and design insights; cloth, rigid-body physics, LEDs, fans, and AC remain client-side.

This release provides a **password-protected, single-instance deployment baseline**, not a public multi-tenant SaaS. Read the production boundaries below before exposing it to a wider audience.

## Local Setup

Use Python 3.11 or later. These commands work in Windows PowerShell without activating the virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Enter your project-scoped OpenAI key into `OPENAI_API_KEY` in your local `.env` using the editor. Keep `DEMO_MODE=false` for real AI generation. Never put credentials in HTML, source code, chat messages, URLs, image layers, or build arguments. The environment files are excluded from Git and the Docker context.

Start the local server:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000. Development API docs are at http://127.0.0.1:8000/docs. If port 8000 is occupied, choose `--port 8001`. Restart the server after changing configuration.

For a preview without an API key, explicitly set `DEMO_MODE=true` in `.env`. These responses use built-in layouts and are visibly labeled **Demo layout**. Missing keys and OpenAI failures never silently switch real generation into demo mode. Production refuses to start in demo mode.

On macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/python` in place of the Windows interpreter path, and `cp .env.example .env`.

## OpenAI Configuration

Create an API project/key in the [OpenAI dashboard](https://platform.openai.com/api-keys), enable billing, and give the key access to the configured model. The default is `gpt-4.1-mini`; another model must support **Chat Completions with strict JSON Schema structured outputs**. Not every model or fine-tuned model supports the schema constraints used here.

The app uses one asynchronous client per process, at most one SDK retry within a total request deadline, and a 5,000-token default output cap. Model output is validated again on the server before reaching the physics engine.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Set `production` when deploying; disables API docs and enables startup checks/HSTS. |
| `OPENAI_API_KEY` | Empty | Secret OpenAI project key; required in production. |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Server-selected structured-output model. |
| `DEMO_MODE` | `false` | Explicit offline preview; prohibited in production. |
| `APP_USERNAME` | `admin` | Single HTTP Basic Auth username. |
| `APP_PASSWORD` | Empty | Production requires at least 16 characters; use a password-manager-generated random ASCII password of 32+ characters. |
| `PUBLIC_ORIGIN` | Empty | Production requires the exact HTTPS origin, such as `https://omnispace.example.com`, without a path. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames, without schemes or ports; include the production hostname. |
| `OPENAI_TIMEOUT_SECONDS` | `30` | Total OpenAI request deadline; range 5-120 seconds. |
| `OPENAI_MAX_OUTPUT_TOKENS` | `5000` | Output cap; range 1000-8000 tokens. |
| `MAX_CONCURRENT_GENERATIONS` | `2` | In-flight OpenAI requests per process; range 1-8. |
| `GENERATION_LIMIT_PER_MINUTE` | `6` | Attempts per authenticated username, or per client address in unauthenticated development. |
| `GENERATION_LIMIT_PER_HOUR` | `60` | Total generation attempts per process; retries consume provider usage too. |
| `PORT` | `8000` | Used by `python main.py` and the container; hosting platforms may supply their own value. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy addresses Uvicorn trusts. Do not use `*` on a directly exposed server. |

Localhost and loopback hostnames remain accepted for container health checks. Basic authentication is required for the editor and generation routes when a password is set. Health endpoints stay public and disclose no credentials. Browser recommendations are rendered as text, not executable HTML.

Set project budget alerts and monitor usage in OpenAI. Provider budget alerts are **not a guaranteed hard spending cap**. The app's quotas are in memory and reset on restart; use a shared budget/rate gateway for durable caps.

## REST API

The client and API are served from the same origin. Cross-origin browser generation requests are rejected; no permissive CORS is enabled.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | 3D editor; protected when `APP_PASSWORD` is set. |
| `POST` | `/generate` | Generate a validated layout; same authentication as the editor. |
| `POST` | `/generate_3d_room` | Backward-compatible alias. |
| `GET` | `/healthz` | Process liveness; no authentication or OpenAI request. |
| `GET` | `/readyz` | Configuration readiness; 200 for OpenAI/demo configuration, otherwise 503. Does not verify key validity, billing, or model access. |
| `GET` | `/docs` | Interactive API reference in development only. |

Request: `Content-Type: application/json`. Send only `text`, a nonblank string of at most 4,000 characters. The complete encoded request body must fit within 16 KiB.

```json
{"text":"A calm bedroom with a queen bed, floral linen, oak nightstands and a reading chair"}
```

Example response:

```json
{
  "layout": [
    {"name":"Reading Chair","w":0.9,"h":1.0,"d":0.8,"x":0.0,"y":0.5,"z":0.0,"color":"#738377","rotation":0.0}
  ],
  "design_recommendation":"Keep a clear walking route around the seating area.",
  "sustainability_insight":"Consider responsibly sourced timber and low-VOC finishes.",
  "source":"openai"
}
```

`source` is `openai` or `demo`. Responses contain at most 24 objects and two beds. All dimensions/coordinates must be finite; objects must fit within a 10.5 m square room below the 3.2 m ceiling. Color values must be six-digit hex strings.

`rotation` is a Y-axis angle in degrees: 0 faces +Z, 90 faces +X, -90 faces -X, and 180 faces -Z. Older imported layouts may omit it and default to 0. The OpenAI schema explicitly requires the field for new generations.

After structural validation, a deterministic placement pass repairs floor heights and placement bounds, separates floor-furniture footprints, reserves clear door approaches, pairs headboards with beds, faces seating toward desks/TVs, and positions accessories on compatible supports. Unsupported screens become wall-mounted. Rugs are intentional underlays. Layouts that cannot fit safely are rejected with an actionable error rather than sent to the physics engine. This is a geometric consistency pass, not architectural-code certification or a guarantee of ideal interior design.

Local PowerShell request without authentication:

```powershell
$body = @{ text = 'An office with a walnut desk and an ergonomic chair' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/generate' -Method Post -ContentType 'application/json' -Body $body
```

For deployed API requests, `curl` can prompt for the application password without placing it directly in command history:

```bash
curl --user admin https://omnispace.example.com/generate \
  --header 'Content-Type: application/json' \
  --data '{"text":"An office with a walnut desk and an ergonomic chair"}'
```

Use `curl.exe` on Windows if `curl` is a PowerShell alias. This is the **application password**, not the OpenAI key. Browsers prompt for the same credentials when opening the editor. Use Basic Auth only over HTTPS outside localhost.

Application errors include a safe `detail` message and `request_id`, also returned in `X-Request-ID`:

```json
{"detail":"AI generation timed out. Please try again.","request_id":"example-request-reference"}
```

| Status | Meaning |
| --- | --- |
| `400` | Untrusted Host header. |
| `401` | Missing/invalid application credentials. |
| `403` | Disallowed browser origin. |
| `413` / `415` | Request too large / non-JSON content type. |
| `422` | Invalid input or provider refusal. |
| `429` | App quota or provider rate limit; honor `Retry-After`. |
| `502` | Invalid/incomplete model output or provider connectivity failure. |
| `503` | Missing provider configuration, provider authentication failure, or concurrency capacity reached. |
| `504` | Total generation deadline exceeded. |
| `500` | Unexpected server failure; use the request reference to inspect server logs. |

Prompts, credentials, and raw model responses are not deliberately logged by the app. Provider data-retention policies still apply. Do not enable verbose provider debug logging in production.

## Docker Deployment

Requires Docker Engine or Docker Desktop with Linux containers. The build runs the unit tests in a verification stage. The final image runs as UID/GID 10001, contains only application code and dependencies, and has a `/readyz` health check.

```bash
docker build -t omnispace3d:1.0 .
```

Prepare a local `.env.production` from the template, outside version control. Set `APP_ENV=production`, `DEMO_MODE=false`, your OpenAI key, a strong application password, `PUBLIC_ORIGIN=https://omnispace.example.com`, and `ALLOWED_HOSTS=omnispace.example.com`. Store those values in the host's secret manager for managed deployments instead of uploading environment files.

Run behind an HTTPS reverse proxy on the same host:

```bash
docker run -d --name omnispace3d --restart unless-stopped \
  --env-file .env.production \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:8000:8000 omnispace3d:1.0
```

On PowerShell, put the command on one line or use backticks instead of backslashes for continuation. The command above assumes `PORT=8000` in the environment file. Terminate TLS with Caddy, Nginx, or your platform ingress; proxy to the loopback port. Use a reverse-proxy body cap around 16 KiB and an upstream timeout above `OPENAI_TIMEOUT_SECONDS`, such as 130 seconds for the maximum configured deadline. Preserve the public Host header, and trust forwarded headers only from actual proxy addresses.

Check liveness and logs:

```bash
curl http://127.0.0.1:8000/readyz
docker logs --tail 100 omnispace3d
```

Missing production secrets, demo mode, weak passwords, non-HTTPS origins, and invalid host configuration cause startup to fail. Do not disable these checks to make a deployment turn green.

## Render Deployment

1. Push the project to a private Git repository. Confirm no `.env` files or secrets are tracked.
2. Create a **Web Service**, connect that repository, choose the **Docker** runtime, and use `./Dockerfile`. No separate frontend service is needed.
3. In the dashboard, add the production variables above. Use the exact assigned `https://<service>.onrender.com` origin/hostname for `PUBLIC_ORIGIN`/`ALLOWED_HOSTS`, or your configured custom domain. Store keys/passwords as secret values.
4. Keep one service instance and one Uvicorn worker. The app reads Render's `PORT` environment value automatically.
5. Set the health-check path to `/readyz` and deploy. Open the HTTPS URL and authenticate with the application credentials.
6. Verify `/healthz` and `/readyz`, then make one authenticated generation request to confirm real model access and billing. That smoke request is billable.
7. Monitor failed requests, latency, 429/503/504 counts, and OpenAI usage. Keep the previous working image/deployment available for rollback.

For a native Python Render service instead, select Python 3.11, use `pip install -r requirements.txt` as the build command, and `python main.py` as the start command. Run the test command in CI before deployment. Render's platform handles public HTTPS.

## Release Checks

```powershell
.\.venv\Scripts\python.exe -m unittest test_main -v
.\.venv\Scripts\python.exe -m pip check
```

The tests mock OpenAI and never require a real key. They cover authentication, production startup requirements, structured output and geometry validation, both generation routes, explicit demo mode, request limits, safe errors, deadlines, rate limits, readiness, and nonblocking concurrency. Recheck dependencies with your vulnerability scanner, pin approved transitive dependencies and the base-image digest in your release process, and rebuild regularly for security updates.

Renderer/physics libraries use versioned URLs and verified subresource-integrity hashes, including the CSS3D renderer used for TV playback. If upgrading a CDN library, recompute its SHA-384 hash and regression-test the renderer and physics. The browser requires outbound access to `cdnjs.cloudflare.com`, `cdn.jsdelivr.net`, and optional photographic textures from `threejs.org`; procedural textures remain available if the photos fail.

## Object Editing

The viewport toolbar includes **Undo** for the last 50 object changes. Moves, color changes, deletions, preset replacement, and generated-layout replacement can be undone. A completed drag or a color-picker adjustment is recorded as one change; an ordinary click is not recorded. Ctrl+Z/Cmd+Z also works when focus is outside a text field or other input. History is kept in browser memory and is cleared on reload. Undo reconstructs furniture safely; restored TVs start powered off rather than resuming external video automatically.

Furniture uses a stable room-planning interaction: an 8 px mouse/pen movement or 12 px touch movement is needed to start dragging. Pointer capture keeps the gesture active outside the object. Dragging follows a horizontal plane at the held height, preserves upright orientation, and stops without a release fling. Rigid-body gravity is 14 m/s2, with firm resting friction and damping; only the held body temporarily uses a low-friction contact pair. Normal gravity and friction resume on release, so an object deliberately moved off a supporting surface can still fall. Rugs do not act as rigid obstacles.

## TV Playback

Rooms containing a TV expose a TV-remote button in the viewport toolbar. Computer monitors do not expose these controls. The remote supports power, episode selection, previous/next, play/pause, mute, volume, screen focus, and fullscreen. Multiple TVs can be selected independently. Closing the remote does not turn off the TV; switching off, deleting the TV, or replacing the room destroys its embedded player.

The screen uses official, privacy-enhanced YouTube embeds, not downloaded or rehosted video. No YouTube API key or OpenAI call is needed for playback. The official player API is loaded only after TV power-on; that dynamic API is trusted by its HTTPS origin rather than a fixed SRI hash. Playback starts muted to accommodate browser autoplay rules; use the remote to enable sound.

The initial catalog consists of these official OGGY uploads, verified against the `@oggy` channel and public oEmbed metadata on 2026-10-24:

- [The Magic Pen (S04E14)](https://www.youtube.com/watch?v=4auOwokj2qg)
- [Mind the Giant! (S04E23)](https://www.youtube.com/watch?v=a8-ySFmij_I)
- [Oggy Goes Green (S04E32)](https://www.youtube.com/watch?v=jfcrY85C_-k)
- [Farmer for a Day (S04E42)](https://www.youtube.com/watch?v=JmB6a6D-N7M)
- [Sport Fans (S04E26)](https://www.youtube.com/watch?v=-7jAVwbqCUE)

These are individual full episodes. The editable, allowlisted catalog is `TV_COLLECTIONS` in the frontend. Replace entries only with authorized, embeddable uploads; do not supply arbitrary iframe URLs from user or AI input.

YouTube controls availability, regional restrictions, advertisements, and embedding permissions. If an upload stops embedding, choose another episode or use the official watch link. The app does not bypass restrictions. The iframe sends an origin referrer with `strict-origin-when-cross-origin` for YouTube player identification. Browser privacy extensions or network policies can still block playback.

The server CSP permits only `https://www.youtube-nocookie.com` as a video-frame origin and `https://www.youtube.com` for the official player API. Restart existing server processes after backend security-policy changes; refreshing the HTML alone is not sufficient. Public hosting must allow those external services and serve the application over HTTPS.

## Production Boundaries

- This baseline protects a single shared account with HTTP Basic Auth. For public/multi-user access, integrate an identity provider or authenticated gateway, per-user authorization, abuse controls, and account lifecycle management before launch.
- Quotas and concurrent-call limits are **per process**, not durable or shared. They reset on restart. Add Redis or a rate/budget gateway before running multiple workers/replicas or relying on hard spending caps.
- The CSP restricts external origins but still allows inline scripts/styles for the existing single-file UI. A stricter public-facing profile requires extracting inline handlers and adopting CSP nonces/hashes. Maintain and periodically audit the legacy Three.js/Cannon.js dependencies, or vendor reviewed assets.
- Room and physics results are conceptual visualizations, not construction documents, safety simulations, or accessibility certification. The server validates shape bounds, not guaranteed collision-free or code-compliant architectural designs.
- Layouts are not persisted. Add storage, access controls, and a retention/privacy policy before offering saved projects or processing sensitive customer floor plans.
- Container/cloud deployment and a real billable OpenAI request must be verified in your hosting environment with your own credentials; mocked tests cannot prove those external integrations.
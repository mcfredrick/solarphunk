# infer.dayjob.dev — Ollama API

Self-hosted Ollama instance behind a Cloudflare Access tunnel.

## Authentication

Every request requires two headers from the service token:

```
CF-Access-Client-Id: <client-id>
CF-Access-Client-Secret: <client-secret>
```

## Important: Use Native `/api/` Paths, Not `/v1/`

Ollama exposes an OpenAI-compatible `/v1/chat/completions` endpoint, but **this path does not work from GitHub Actions or any Python httpx/requests client**. Cloudflare's bot management blocks Python's TLS fingerprint (JA3) when hitting the `/v1/` path.

**Use the native Ollama API instead:**

| Task | Path | Works from GHA? |
|------|------|-----------------|
| List models | `GET /api/tags` | ✓ |
| Chat | `POST /api/chat` | ✓ |
| OpenAI-compat chat | `POST /v1/chat/completions` | ✗ (TLS fingerprint blocked) |

curl works for all paths because libcurl has a different TLS fingerprint than Python's ssl module.

## Examples

List available models:

```bash
curl https://infer.dayjob.dev/api/tags \
  -H "CF-Access-Client-Id: $CF_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_CLIENT_SECRET"
```

Chat completion:

```bash
curl https://infer.dayjob.dev/api/chat \
  -H "CF-Access-Client-Id: $CF_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{ "role": "user", "content": "Hello" }],
    "stream": false,
    "options": { "num_predict": 512 }
  }'
```

## Python (httpx — correct approach)

```python
import httpx, os

headers = {
    "Content-Type": "application/json",
    "CF-Access-Client-Id": os.environ["CF_CLIENT_ID"],
    "CF-Access-Client-Secret": os.environ["CF_CLIENT_SECRET"],
}

r = httpx.post(
    "https://infer.dayjob.dev/api/chat",
    headers=headers,
    json={
        "model": "qwen3:8b",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ],
        "stream": False,
        "options": {"num_predict": 512},
    },
    timeout=300,
)
print(r.json()["message"]["content"])
```

## Python (OpenAI SDK — does NOT work from GHA)

```python
# DO NOT USE from GitHub Actions — CF bot management blocks Python TLS fingerprint
# on the /v1/ path. Use httpx + /api/chat instead (see above).
from openai import OpenAI
client = OpenAI(base_url="https://infer.dayjob.dev/v1", api_key="ollama")
```

## Notes

- Credentials are stored in `.secrets` (gitignored) on local machines
- GHA secrets: `CF_CLIENT_ID`, `CF_CLIENT_SECRET`
- The solarphunk pipeline uses `lib/llm._call_ollama()` which handles this correctly

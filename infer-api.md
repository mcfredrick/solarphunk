# infer.dayjob.dev — Ollama API

Self-hosted Ollama instance behind a Cloudflare Access tunnel.

## Authentication

Every request requires two headers from the service token:

```
CF-Access-Client-Id: <client-id>
CF-Access-Client-Secret: <client-secret>
```

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
    "model": "llama3.1:latest",
    "messages": [{ "role": "user", "content": "Hello" }],
    "stream": false
  }'
```

## Python (OpenAI SDK)

```python
import httpx
from openai import OpenAI

client = OpenAI(
    base_url="https://infer.dayjob.dev/v1",
    api_key="ollama",
    http_client=httpx.Client(headers={
        "CF-Access-Client-Id": CF_CLIENT_ID,
        "CF-Access-Client-Secret": CF_CLIENT_SECRET,
    })
)

response = client.chat.completions.create(
    model="llama3.1:latest",
    messages=[{"role": "user", "content": "Hello"}]
)
```

Credentials are stored in `.secrets` (gitignored).

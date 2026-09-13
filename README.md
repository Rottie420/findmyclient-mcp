# FindMyClient MCP Server (multi-tenant)

Exposes the FindMyClient.org API as MCP tools. **Every tool takes `api_token` as its
first argument** -- each caller passes their own FindMyClient token. This server has
no shared credential of its own and never touches your credits when someone else
uses it.

- `search_leads(api_token, query, max_pages?, max_websites?, max_results?)` -- starts an async search job
- `get_search_status(api_token, job_id)` -- polls job status
- `get_enriched_leads(api_token, job_id)` -- fetches validated/scored leads for a completed job
- `search_and_wait(api_token, query, ...)` -- does all three in one call (recommended)

Transport: **streamable-http** -- works as a remote MCP connector, deployable to Cloud Run.

## Local run

```bash
pip install -r requirements.txt
python server.py
# serves MCP at http://0.0.0.0:8080/mcp
```

No token needed at startup -- callers supply their own per tool call.

## Deploy to Cloud Run

1. Set GitHub repo secrets: `GCP_PROJECT_ID`, `GCP_SA_KEY` (service account JSON with
   Cloud Run + Artifact Registry/GCR deploy permissions).
2. Push to `main` -- `.github/workflows/deploy.yml` builds, pushes, and deploys to
   Cloud Run (`asia-southeast1` by default).

No secrets to manage on the server side -- there's no shared FindMyClient token to protect.

## Sharing this with others

Give people the deployed URL + `/mcp` path and tell them: "connect this as an MCP
server, then get your own FindMyClient API token from your dashboard's API Tokens
page and pass it as `api_token` when you use the tools."

Because tokens are per-call arguments (not stored server-side), this is safe to share
broadly:
- No one can spend your credits -- they authenticate with their own token.
- You're only hosting compute (the Cloud Run container), not liability for API usage.
- If someone's MCP client doesn't want to expose a raw token in every call, they can
  wrap it client-side (e.g. a small config that auto-fills `api_token` from their own
  env var) -- that's on the client, not this server.

## Connect it in Claude

Add as a custom connector using the deployed Cloud Run URL + `/mcp` path, streamable-http
transport. When prompted by an agent for `api_token`, supply your own FindMyClient token.

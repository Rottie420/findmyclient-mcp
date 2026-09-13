"""
FindMyClient MCP server (multi-tenant).

Wraps the FindMyClient.org lead-generation API (search -> poll -> enriched leads)
as MCP tools. Each caller supplies their OWN FindMyClient API token as a tool
argument -- this server holds no shared credential and never sees credits spent
by someone else.

API docs: https://docs.findmyclient.org/api-token/
"""

import asyncio
import time
import os
import httpx
from mcp.server.mcpserver import MCPServer

BASE_URL = "https://findmyclient.org/api"
PORT = int(os.environ.get("PORT", 8080))

mcp = MCPServer("findmyclient")


def _headers(api_token: str) -> dict:
    return {"token": api_token, "Content-Type": "application/json"}


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    """Run an HTTP call and turn any failure into a returned {"error": ...} dict
    instead of an uncaught exception -- MCP clients otherwise only see a generic
    "Error executing tool" with no detail.
    """
    try:
        resp = await client.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        return {"error": f"FindMyClient API returned {e.response.status_code}", "detail": detail}
    except httpx.RequestError as e:
        return {"error": f"Could not reach FindMyClient API: {e}"}


async def search_leads(
    api_token: str,
    query: str,
    max_pages: int | None = None,
    max_websites: int | None = None,
    max_results: int | None = None,
) -> dict:
    """Start an async FindMyClient lead-search job for a query (e.g. "singapore cafe",
    "solar installers texas"), using your own FindMyClient API token. Returns a job_id,
    status, and credits_remaining. The job runs asynchronously -- use
    get_search_status(job_id) to poll, then get_enriched_leads(job_id) once complete.
    For a single call that does all of this, use search_and_wait instead.

    api_token: your personal FindMyClient API token (from your dashboard's API Tokens
    section). Required on every call -- this server does not store or share tokens.
    """
    payload: dict = {"query": query}
    if max_pages is not None:
        payload["max_pages"] = max_pages
    if max_websites is not None:
        payload["max_websites"] = max_websites
    if max_results is not None:
        payload["max_results"] = max_results

    async with httpx.AsyncClient(timeout=30) as client:
        return await _request(
            client, "POST", f"{BASE_URL}/search", json=payload, headers=_headers(api_token)
        )


async def get_search_status(api_token: str, job_id: str) -> dict:
    """Check the status of a FindMyClient search job, using your own API token.
    Status is one of 'processing', 'completed', or 'failed'. Once 'completed',
    call get_enriched_leads(job_id) to retrieve the validated lead data.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        return await _request(
            client, "GET", f"{BASE_URL}/result/{job_id}", headers=_headers(api_token)
        )


async def get_enriched_leads(api_token: str, job_id: str) -> dict:
    """Fetch validated, classified, confidence-scored leads for a completed
    FindMyClient job_id, using your own API token. Only call this after
    get_search_status reports status 'completed'.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        return await _request(
            client, "GET", f"{BASE_URL}/result/leads/{job_id}", headers=_headers(api_token)
        )


@mcp.tool()
async def search_and_wait(
    api_token: str,
    query: str,
    max_pages: int | None = None,
    max_websites: int | None = None,
    max_results: int | None = None,
    timeout_seconds: int = 10000,
) -> dict:
    """Run a full FindMyClient search in one call using your own API token: submit
    the job, poll until it completes (or fails/times out), then return the enriched
    leads. Best for a single "find me leads for X" request. The tool waits until the
    job completes or the timeout is reached.
    """
    start = await search_leads(api_token, query, max_pages, max_websites, max_results)
    job_id = start.get("job_id")
    if not job_id:
        return {"error": "No job_id returned from /search", "response": start}

    deadline = time.time() + timeout_seconds

    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:

            print(f"[FindMyClient] Polling job {job_id}...")

            data = await _request(
                client,
                "GET",
                f"{BASE_URL}/result/{job_id}",
                headers=_headers(api_token),
            )

            print(f"[FindMyClient] Response: {data}")

            if "error" in data and "status" not in data:
                return {
                    "error": data["error"],
                    "detail": data.get("detail"),
                    "job_id": job_id,
                }

            status = data.get("status")

            print(f"[FindMyClient] Status: {status}")

            if status == "completed":
                if data.get("error"):
                    return {
                        "error": data["error"],
                        "job_id": job_id,
                    }

                leads_data = await _request(
                    client,
                    "GET",
                    f"{BASE_URL}/result/leads/{job_id}",
                    headers=_headers(api_token),
                )

                return {
                    "job_id": job_id,
                    "credit_cost": data.get("credit_cost"),
                    **leads_data,
                }

            if status == "failed":
                return {
                    "error": data.get("error", "job failed"),
                    "job_id": job_id,
                }

            # Still running
            await asyncio.sleep(30)

    return {
        "error": "Timed out waiting for job to complete",
        "job_id": job_id,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=PORT)

"""
FindMyClient MCP server (multi-tenant).

Wraps the FindMyClient.org lead-generation API (search -> poll -> enriched leads)
as MCP tools. Each caller supplies their OWN FindMyClient API token as a tool
argument -- this server holds no shared credential and never sees credits spent
by someone else.

API docs: https://docs.findmyclient.org/api-token/
"""

import asyncio
import logging
import time
import os
import httpx
from mcp.server.mcpserver import MCPServer

BASE_URL = "https://findmyclient.org/api"
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("findmyclient")

mcp = MCPServer("findmyclient")


def _headers(api_token: str) -> dict:
    return {"token": api_token, "Content-Type": "application/json"}


def _sanitize(value, api_token: str):
    """Strip the caller's API token out of anything we're about to hand back
    to the model/client, in case the upstream API ever echoes request
    headers or the token itself in an error body.
    """
    if not api_token:
        return value
    text = str(value)
    if api_token in text:
        return text.replace(api_token, "***")
    return value


async def _request(client: httpx.AsyncClient, method: str, url: str, api_token: str, **kwargs) -> dict:
    """Run an HTTP call and turn any failure into a returned {"error": ...} dict
    instead of an uncaught exception -- MCP clients otherwise only see a generic
    "Error executing tool" with no detail. Error detail is sanitized so the
    caller's own token can never be reflected back to them.
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
        logger.warning("FindMyClient API error %s for %s %s", e.response.status_code, method, url)
        return {
            "error": f"FindMyClient API returned {e.response.status_code}",
            "detail": _sanitize(detail, api_token),
        }
    except httpx.RequestError as e:
        logger.warning("FindMyClient API unreachable: %s", e)
        return {"error": f"Could not reach FindMyClient API: {_sanitize(e, api_token)}"}


async def search_leads(
    api_token: str,
    query: str,
    max_pages: int | None = None,
    max_websites: int | None = None,
    max_results: int | None = None,
) -> dict:
    """Internal helper -- not exposed as an MCP tool. Starts an async FindMyClient
    lead-search job for a query (e.g. "singapore cafe", "solar installers texas").
    Returns a job_id, status, and credits_remaining.

    This is called by search_and_wait, the one tool this server exposes; it is
    kept as a plain function (rather than @mcp.tool()) so a job can't be started
    without also being polled to completion in the same call.

    api_token: your personal FindMyClient API token (from your dashboard's API
    Tokens section). Required on every call -- this server does not store or
    share tokens, and never returns a token in any response.
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
            client, "POST", f"{BASE_URL}/search", api_token, json=payload, headers=_headers(api_token)
        )


async def get_search_status(api_token: str, job_id: str) -> dict:
    """Internal helper -- not exposed as an MCP tool. Checks the status of a
    FindMyClient search job. Status is one of 'processing', 'completed', or
    'failed'. Used internally by search_and_wait's poll loop.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        return await _request(
            client, "GET", f"{BASE_URL}/result/{job_id}", api_token, headers=_headers(api_token)
        )


async def get_enriched_leads(api_token: str, job_id: str) -> dict:
    """Internal helper -- not exposed as an MCP tool. Fetches validated,
    classified, confidence-scored leads for a completed FindMyClient job_id.
    Only called internally once get_search_status reports 'completed'.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        return await _request(
            client, "GET", f"{BASE_URL}/result/leads/{job_id}", api_token, headers=_headers(api_token)
        )


@mcp.tool()
async def search_and_wait(
    api_token: str,
    query: str,
    max_pages: int | None = None,
    max_websites: int | None = None,
    max_results: int | None = None,
    timeout_seconds: int = 240,
) -> dict:
    """Run a full FindMyClient search in one call using your own API token:
    submits the job, polls until it completes (or fails/times out), then
    returns the enriched leads. This is the only tool this server exposes --
    there is no separate "check status" or "get leads" tool, so don't try to
    call one; everything happens inside this single call.

    Typical searches finish well within the default 240s timeout. If a search
    times out anyway, the response still includes job_id -- call
    search_and_wait again isn't useful for the *same* job (it starts a new
    search), so if you need to recover a specific slow job, that requires a
    direct call to the FindMyClient API's /result/{job_id} endpoint outside
    this tool.

    api_token: your personal FindMyClient API token (from your dashboard's API
    Tokens section). Required on every call -- this server does not store or
    share tokens between callers, and never echoes a token back in a response.

    query: what to search for, e.g. "singapore cafe" or "solar installers texas".

    max_pages / max_websites / max_results: optional caps on search depth and
    result count. Leave unset to use the API's defaults.

    timeout_seconds: how long this call is willing to keep polling before
    giving up and returning an error with the job_id. Keep this under your
    MCP client's own request timeout, or the client will kill the call first
    and you'll get a generic timeout with no job_id at all.
    """
    start = await search_leads(api_token, query, max_pages, max_websites, max_results)
    job_id = start.get("job_id")
    if not job_id:
        return {"error": "No job_id returned from /search", "response": start}

    logger.info("Started FindMyClient job %s for query=%r", job_id, query)
    deadline = time.time() + timeout_seconds
    poll_interval = 2.0

    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            data = await _request(
                client,
                "GET",
                f"{BASE_URL}/result/{job_id}",
                api_token,
                headers=_headers(api_token),
            )

            if "error" in data and "status" not in data:
                logger.warning("Job %s errored before completion: %s", job_id, data.get("error"))
                return {
                    "error": data["error"],
                    "detail": data.get("detail"),
                    "job_id": job_id,
                }

            status = data.get("status")
            logger.debug("Job %s status=%s", job_id, status)

            if status == "completed":
                if data.get("error"):
                    logger.warning("Job %s completed with error: %s", job_id, data["error"])
                    return {
                        "error": data["error"],
                        "job_id": job_id,
                    }

                leads_data = await _request(
                    client,
                    "GET",
                    f"{BASE_URL}/result/leads/{job_id}",
                    api_token,
                    headers=_headers(api_token),
                )
                logger.info("Job %s completed, credit_cost=%s", job_id, data.get("credit_cost"))

                return {
                    "job_id": job_id,
                    "credit_cost": data.get("credit_cost"),
                    **leads_data,
                }

            if status == "failed":
                logger.warning("Job %s failed", job_id)
                return {
                    "error": data.get("error", "job failed"),
                    "job_id": job_id,
                }

            # Still running -- back off gradually to keep latency low on
            # fast jobs without hammering the API on slow ones.
            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 30)

    logger.warning("Job %s timed out after %ss", job_id, timeout_seconds)
    return {
        "error": "Timed out waiting for job to complete",
        "job_id": job_id,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=PORT)
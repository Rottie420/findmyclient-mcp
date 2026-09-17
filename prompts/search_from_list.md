CONFIG
TOKEN — YOUR-API-TOKEN
MAX_PAGES — Max Places API pages to paginate per query (default: 20)
MAX_WEBSITES — Max websites to crawl per query (default: 20)
MAX_RESULTS — Max pages to crawl per website (default: 20)

TASK
Using the FindMyClient API with the config above, process the list of search queries generated earlier:

1. Split the queries into reasonably sized batches for submission.
2. For each batch, submit every query to FindMyClient's search endpoint using MAX_PAGES, MAX_WEBSITES, and MAX_RESULTS as the crawl/enrichment limits, to find and enrich matching businesses.
3. Track progress across batches — note any queries that fail, time out, or return zero results, and retry once before giving up on them.
4. Consolidate all enriched results into a single table with columns: Query, Business Name, Website, Location, and any contact/enrichment fields FindMyClient returns.
5. After the table, add a short summary: total businesses found, queries with no results, and any errors encountered.

Do not fabricate data — only include what the API actually returns. If TOKEN is invalid or missing, stop immediately and report the error instead of proceeding.
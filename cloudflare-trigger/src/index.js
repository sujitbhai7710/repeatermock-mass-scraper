/**
 * Cloudflare Worker: RepeaterMock Scraper Trigger
 *
 * Receives POST /trigger from the GitHub Actions MERGE job and triggers the next
 * matrix run via GitHub API (workflow_dispatch).
 *
 * Authentication:
 *   - Bearer token in Authorization header must match TRIGGER_TOKEN secret
 *   - GitHub API call uses GH_PAT secret (classic PAT with repo+workflow scope)
 *
 * Endpoint:
 *   POST /trigger
 *   Body: { "max_tests_per_job": "" }  (optional input override)
 *   Headers: Authorization: Bearer <TRIGGER_TOKEN>
 *
 * Response:
 *   204 → GitHub accepted the trigger (run will start within ~30s)
 *   401 → wrong/missing bearer token
 *   502 → GitHub API call failed
 */

const GITHUB_OWNER = "sujitbhai7710";
const GITHUB_REPO = "repeatermock-mass-scraper";
const WORKFLOW_FILE = "scrape-matrix.yml";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Health check (no auth)
    if (url.pathname === "/" && request.method === "GET") {
      return json({ status: "ok", service: "rm-scraper-trigger" });
    }

    if (url.pathname !== "/trigger" || request.method !== "POST") {
      return json({ error: "Not found", hint: "POST /trigger with Authorization: Bearer <TRIGGER_TOKEN>" }, 404);
    }

    // Bearer token auth
    const authHeader = request.headers.get("Authorization") || "";
    const expectedAuth = `Bearer ${env.TRIGGER_TOKEN}`;
    if (!env.TRIGGER_TOKEN || authHeader !== expectedAuth) {
      return json({ error: "Unauthorized" }, 401);
    }

    // Parse optional input
    let maxTests = "";
    try {
      const body = await request.json();
      maxTests = String(body?.max_tests_per_job || "").trim();
    } catch {
      // Body is optional — default to empty (unlimited)
    }

    // Trigger workflow_dispatch via GitHub API
    const ghResp = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GH_PAT}`,
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "cloudflare-worker-rm-trigger",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: { max_tests_per_job: maxTests },
        }),
      }
    );

    if (ghResp.status === 204) {
      return json({ ok: true, triggered: true, message: "Next matrix run triggered on GitHub" });
    }

    // Error from GitHub
    const ghText = await ghResp.text();
    return json({
      error: "GitHub API call failed",
      status: ghResp.status,
      body: ghText.slice(0, 500),
    }, 502);
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

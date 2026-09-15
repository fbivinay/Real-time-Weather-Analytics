// The cluster API is plain HTTP on a NodePort. A browser on an HTTPS Vercel
// page refuses to fetch it (mixed content), so the request is made here,
// server-side, where that rule does not apply. This is the only reason the
// proxy exists - it adds no logic of its own.
const API_BASE = process.env.WEATHER_API_URL || "http://35.170.210.110:30080";

const ALLOWED = new Set(["stations", "alerts", "stats", "health"]);

export const dynamic = "force-dynamic";

export async function GET(request) {
  const resource = new URL(request.url).searchParams.get("resource");

  // Allowlist, not passthrough: without it this route would happily fetch any
  // URL on the cluster network on behalf of an anonymous caller.
  if (!ALLOWED.has(resource)) {
    return Response.json({ error: "Unknown resource" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${API_BASE}/api/${resource}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!upstream.ok) {
      return Response.json(
        { error: `Upstream returned ${upstream.status}` },
        { status: 502 },
      );
    }
    return Response.json(await upstream.json());
  } catch (err) {
    // The cluster is a single node that gets torn down between sessions, so
    // "unreachable" is an expected state the dashboard must render, not a crash.
    return Response.json(
      { error: "Cluster unreachable", detail: String(err.message || err) },
      { status: 503 },
    );
  }
}

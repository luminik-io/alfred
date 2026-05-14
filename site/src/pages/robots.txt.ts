import type { APIRoute } from "astro";

// Served at /robots.txt. Allows all crawlers and points them at the
// Starlight-generated sitemap index. `site` is resolved from astro.config.mjs
// (ALFRED_OS_SITE_URL override or the alfred.luminik.io default), so a fork
// deploying under its own domain gets a correct Sitemap line for free.
export const GET: APIRoute = ({ site }) => {
  const sitemap = site ? new URL("sitemap-index.xml", site).href : "/sitemap-index.xml";
  const body = `User-agent: *
Allow: /

Sitemap: ${sitemap}
`;
  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};

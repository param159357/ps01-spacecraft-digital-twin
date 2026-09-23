import type { NextConfig } from "next";

// `npm run build` sets NEXT_EXPORT=1 to produce the static export Caddy serves.
// `npm run dev` leaves it unset and instead proxies /api to the local backend, so the
// browser sees one origin in development and the session cookie behaves exactly as it
// does behind Caddy in deployment. Rewrites are unsupported in an export build, which
// is why the two modes are mutually exclusive here.
const isExport = process.env.NEXT_EXPORT === "1";
const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Fixed trailing-slash routes keep /master/ resolvable as a static file and make a
  // direct browser refresh work behind the reverse proxy.
  trailingSlash: true,
  ...(isExport
    ? {
        output: "export" as const,
        // Its own build directory so `npm run build` cannot corrupt a running
        // `npm run dev`: both would otherwise write to .next and collide.
        distDir: ".next-export",
      }
    : {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${apiProxyTarget}/api/:path*` }];
        },
      }),
};

export default nextConfig;

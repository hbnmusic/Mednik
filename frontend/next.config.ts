import type { NextConfig } from "next";

// The dashboard calls the FastAPI backend through same-origin /api/* paths, proxied here.
// Works the same locally (localhost:3000 → 127.0.0.1:8000) and in GitHub Codespaces, where the
// browser cannot reach the container's localhost directly.
const API_INTERNAL = (process.env.MEDNIK_API_INTERNAL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    proxyTimeout: 600_000, // live syncs and study-concept registry queries can take minutes
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_INTERNAL}/api/:path*` }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

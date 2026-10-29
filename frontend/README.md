# mednik frontend

Next.js (App Router, TypeScript) + Tailwind v4 + Radix primitives + ECharts. See the repository root README for setup.

The dashboard calls `/api/*` on its own origin; `next.config.ts` proxies those requests to the FastAPI backend (`MEDNIK_API_INTERNAL`, default `http://127.0.0.1:8000`). This is what lets the same build work locally and in GitHub Codespaces.

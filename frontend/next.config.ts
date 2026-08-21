import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output ONLY for the Docker image (frontend/Dockerfile sets
  // NEXT_OUTPUT=standalone). Vercel must NOT use standalone — it breaks
  // Vercel's build trace (next-server.js.nft.json). See DEPLOY.md.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // Local dev proxy: forward /api/backend/* to the local FastAPI during
  // `next dev`. On Vercel, the vercel.json `services` rewrite handles
  // /api/backend routing to the backend service (this rewrite is dev-only).
  async rewrites() {
    return [
      {
        source: "/api/backend/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

export default nextConfig;

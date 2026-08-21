import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output ONLY for the Docker image (frontend/Dockerfile sets
  // NEXT_OUTPUT=standalone). Vercel must NOT use standalone — it breaks
  // Vercel's build trace (next-server.js.nft.json). See DEPLOY.md.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
};

export default nextConfig;

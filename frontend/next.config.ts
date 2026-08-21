import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Lean runtime image for the Docker deploy path (frontend/Dockerfile);
  // no effect on Vercel builds. See DEPLOY.md.
  output: "standalone",
};

export default nextConfig;

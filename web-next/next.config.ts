import type { NextConfig } from "next";

// Static export: `next build` writes plain HTML/JS/CSS to ./out, which the Python server
// serves at / on the same origin as the API and the /ws socket. No Node process sits in the
// data path, so a push from Bybit reaches the screen through one hop (Python -> browser).
const nextConfig: NextConfig = {
  output: "export",
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;

/**
 * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation. This is especially useful
 * for Docker builds.
 */
await import("./src/env.mjs");
import { env } from "./src/env.mjs";
import bundleAnalyzer from "@next/bundle-analyzer";

/**
 * CSP headers
 * img-src https to allow loading images from SSO providers
 */
const cspHeader = `
  default-src 'self' https://*.langfuse.com https://*.langfuse.dev https://*.posthog.com;
  script-src 'self' 'unsafe-eval' 'unsafe-inline' https://*.langfuse.com https://*.langfuse.dev https://challenges.cloudflare.com https://static.cloudflareinsights.com https://*.stripe.com https://uptime.betterstack.com https://chat.cdn-plain.com;
  style-src 'self' 'unsafe-inline' https://uptime.betterstack.com https://fonts.googleapis.com;
  img-src 'self' https: blob: data: http://localhost:* https://prod-uk-services-workspac-workspacefilespublicbuck-vs4gjqpqjkh6.s3.amazonaws.com https://prod-uk-services-attachm-attachmentsbucket28b3ccf-uwfssb4vt2us.s3.eu-west-2.amazonaws.com https://i0.wp.com;
  font-src 'self';
  frame-src 'self' https://challenges.cloudflare.com https://*.stripe.com;
  worker-src 'self' blob:;
  object-src 'none';
  base-uri 'self';
  form-action 'self';
  frame-ancestors 'none';
  connect-src 'self' https://*.langfuse.com https://*.langfuse.dev https://uptime.betterstack.com https://chat.uk.plain.com https://*.s3.amazonaws.com https://prod-uk-services-attachm-attachmentsuploadbucket2-1l2e4906o2asm.s3.eu-west-2.amazonaws.com;
  media-src 'self' https: http://localhost:*;
  ${env.LANGFUSE_CSP_ENFORCE_HTTPS === "true" ? "upgrade-insecure-requests; block-all-mixed-content;" : ""}
`;

// Match rules for Hugging Face
const huggingFaceHosts = ["huggingface.co", ".*\\.hf\\.space$"];

/** @type {import("next").NextConfig} */
const nextConfig = {
  eslint: {
    ignoreDuringBuilds: process.env.SKIP_BUILD_LINT === "true",
  },
  staticPageGenerationTimeout: 500, // default is 60. Required for build process for amd
  transpilePackages: ["@langfuse/shared", "vis-network/standalone"],
  reactStrictMode: true,
  experimental: {
    instrumentationHook: true,
    serverComponentsExternalPackages: [
      "dd-trace",
      "@opentelemetry/api",
      "@appsignal/opentelemetry-instrumentation-bullmq",
      "bullmq",
      "@opentelemetry/sdk-node",
      "@opentelemetry/instrumentation-winston",
      "kysely",
    ],
  },
  poweredByHeader: false,
  basePath: env.NEXT_PUBLIC_BASE_PATH,

  /**
   * If you have `experimental: { appDir: true }` set, then you must comment the below `i18n` config
   * out.
   *
   * @see https://github.com/vercel/next.js/issues/41980
   */
  i18n: {
    locales: ["en"],
    defaultLocale: "en",
  },
  output: "standalone",

  async headers() {
    return [
      {
        // Add noindex for all pages except root and /auth*
        source: "/:path((?!auth|^$).*)*",
        headers: [
          {
            key: "X-Robots-Tag",
            value: "noindex",
          },
        ],
      },
      {
        source: "/:path*",
        headers: [
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Document-Policy",
            value: "js-profiling",
          },
          {
            key: "Permissions-Policy",
            value: "autoplay=*, fullscreen=*, microphone=*",
          },
        ],
      },
      {
        source: "/:path*",
        headers: [
          {
            key: "x-frame-options",
            value: "SAMEORIGIN",
          },
        ],
        // Disable x-frame-options on Hugging Face to allow for embedded use of Langfuse
        missing: huggingFaceHosts.map((host) => ({
          type: "host",
          value: host,
        })),
      },
      // CSP header
      {
        source: "/:path((?!api).*)*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: cspHeader.replace(/\n/g, ""),
          },
        ],
        // Disable CSP on Hugging Face to allow for embedded use of Langfuse
        missing: huggingFaceHosts.map((host) => ({
          type: "host",
          value: host,
        })),
      },
      // Required to check authentication status from langfuse.com
      ...(env.NEXT_PUBLIC_LANGFUSE_CLOUD_REGION !== undefined
        ? [
            {
              source: "/api/auth/session",
              headers: [
                {
                  key: "Access-Control-Allow-Origin",
                  value: "https://langfuse.com",
                },
                { key: "Access-Control-Allow-Credentials", value: "true" },
                { key: "Access-Control-Allow-Methods", value: "GET,POST" },
                {
                  key: "Access-Control-Allow-Headers",
                  value: "Content-Type, Authorization",
                },
              ],
            },
          ]
        : []),
      // all files in /public/generated are public and can be accessed from any origin, e.g. to render an API reference based on our openapi schema
      {
        source: "/generated/:path*",
        headers: [
          {
            key: "Access-Control-Allow-Origin",
            value: "*",
          },
          {
            key: "Access-Control-Allow-Methods",
            value: "GET",
          },
        ],
      },
    ];
  },

  // webassembly support for @dqbd/tiktoken
  webpack(config, { isServer }) {
    config.experiments = {
      asyncWebAssembly: true,
      layers: true,
    };

    // Exclude Datadog packages from webpack bundling to avoid issues
    config.externals.push("@datadog/pprof", "dd-trace");

    return config;
  },
};

// Enable bundle analyzer in analyze mode, otherwise use standard config
const withBundleAnalyzer = bundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
  openAnalyzer: false, // Open analyzer in browser
});

export default withBundleAnalyzer(nextConfig);

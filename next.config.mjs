/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone', // required by the Docker runner stage
  reactStrictMode: true,
  poweredByHeader: false,
  logging: { fetches: { fullUrl: false } },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
        ],
      },
    ]
  },
}
export default nextConfig

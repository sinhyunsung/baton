/** @type {import('next').NextConfig} */
// 주의: rewrites는 빌드 시점에 평가된다. compose 배포 기준 서비스명을 기본값으로 사용.
const backend = process.env.BACKEND_INTERNAL_URL || "http://baton_backend:8000";

const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;

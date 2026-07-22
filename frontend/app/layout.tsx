import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import ChatWidget from "@/components/ChatWidget";
import TopSearch from "@/components/TopSearch";

export const metadata: Metadata = {
  title: "Baton — 사내 지식 위키",
  description: "업무 데이터를 클레임으로 구조화한 살아있는 사내 위키",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700&display=swap" />
      </head>
      <body>
        <div className="shell">
          <div className="appwrap">
            <div className="topnav">
              <Link href="/wiki">
                <div className="wordmark">
                  <svg width="26" height="26" viewBox="0 0 20 20" fill="none">
                    <circle cx="5" cy="15" r="2.6" fill="#0064E0" />
                    <circle cx="15" cy="5" r="2.6" fill="#0064E0" />
                    <path d="M6.7 13.3 13.3 6.7" stroke="#0064E0" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  Baton
                </div>
              </Link>
              <div style={{ flex: 1 }} />
              <TopSearch />
              <div className="avatar">나</div>
            </div>
            <div className="bodyrow">
              <Sidebar />
              <div className="main">{children}</div>
              <ChatWidget />
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}

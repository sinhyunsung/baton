"use client";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const NAV: { group: string; items: { href: string; label: string; badge?: "proposals" | "issues" }[] }[] = [
  { group: "위키", items: [
    { href: "/wiki", label: "트리" },
    { href: "/search", label: "검색" },
  ]},
  { group: "데이터", items: [
    { href: "/admin", label: "데이터 소스" },
    { href: "/ingest", label: "인제스트 현황" },
    { href: "/proposals", label: "위키 갱신안", badge: "proposals" },
  ]},
  { group: "품질", items: [
    { href: "/lint", label: "린트 실행" },
    { href: "/inbox", label: "확인 요청함 · 역질문", badge: "issues" },
  ]},
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [badges, setBadges] = useState({ proposals: 0, issues: 0 });

  useEffect(() => {
    const load = async () => {
      try {
        const [p, i] = await Promise.all([
          api("/api/proposals?status=pending"),
          api("/api/lint/issues?status=open"),
        ]);
        setBadges({ proposals: p.pending_count ?? 0, issues: i.issues?.length ?? 0 });
      } catch {}
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [pathname]);

  return (
    <aside className="sidenav">
      {NAV.map((g) => (
        <div key={g.group}>
          {g.items.map((it) => {
            const n = it.badge ? badges[it.badge] : 0;
            return (
              <button key={it.href} onClick={() => router.push(it.href)}
                className={`navitem ${pathname.startsWith(it.href) ? "on" : ""}`}>
                {it.label}
                {n > 0 && <span className="navnum">{n}</span>}
              </button>
            );
          })}
        </div>
      ))}
    </aside>
  );
}

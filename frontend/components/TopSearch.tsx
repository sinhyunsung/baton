"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function TopSearch() {
  const [q, setQ] = useState("");
  const router = useRouter();
  return (
    <div className="searchpill">
      <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" strokeLinecap="round" /></svg>
      <input placeholder="지식 검색" value={q} onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) router.push(`/search?q=${encodeURIComponent(q)}`); }} />
    </div>
  );
}

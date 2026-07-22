"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import Link from "next/link";
import { api, sse, SOURCE_LABEL } from "@/lib/api";

function SearchInner() {
  const sp = useSearchParams();
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<any>(null);
  const [noEv, setNoEv] = useState<string | null>(null);
  const [filter, setFilter] = useState("전체");

  async function run(query?: string) {
    const text = (query ?? q).trim();
    if (!text || busy) return;
    setBusy(true); setAnswer(""); setResult(null); setNoEv(null);
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(text)}`);
      setResult(r);
      await sse("/api/chat", { question: text }, (event, data) => {
        if (event === "token") setAnswer((a) => a + data);
        else if (event === "no_evidence") setNoEv(JSON.parse(data).message);
      });
    } catch { setNoEv("검색 중 오류가 발생했습니다."); }
    setBusy(false);
  }
  useEffect(() => { if (sp.get("q")) run(sp.get("q")!); }, [sp]);

  const matches = (result?.matches ?? []).filter((m: any) => filter !== "확인됨만" || m.human_confirmed);

  return (
    <section className="scr">
      <div className="kicker">위키 · 검색</div>
      <div className="h1 mb12">위키 검색</div>
      <div className="fx gap10 mb16" style={{ maxWidth: 720 }}>
        <div className="searchpill" style={{ maxWidth: "none", height: 48 }}>
          <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" strokeLinecap="round" /></svg>
          <input value={q} style={{ fontSize: 16 }} placeholder="예) 온보딩 총괄 담당자 누구"
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} />
        </div>
        <button className="btn btn-cta" onClick={() => run()} disabled={busy}>{busy ? "검색 중…" : "검색"}</button>
      </div>
      <div className="fx gap6 mb24">
        {["전체", "확인됨만"].map((f) => (
          <button key={f} className={`pill ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>{f}</button>
        ))}
      </div>

      {noEv && (
        <div className="card-lg pad24 mb24" style={{ maxWidth: 860, background: "var(--soft)", border: "none" }}>
          <span className="bdg bdg-attn mb10" style={{ marginBottom: 10 }}>근거 없음</span>
          <div className="fs16 mt10">{noEv} 챗봇(우측 하단)에서 역질문을 생성할 수 있어요.</div>
        </div>
      )}

      {(answer || (busy && result)) && !noEv && (
        <div className="card-lg pad24 mb24" style={{ maxWidth: 860, background: "var(--soft)", border: "none" }}>
          <div className="fx ac gap8 mb10"><span className="bdg bdg-ink">AI 요약 답변</span></div>
          <div className="fs16 md" style={{ lineHeight: 1.5 }}><ReactMarkdown>{answer || "…"}</ReactMarkdown></div>
          {result?.matches?.[0] && (
            <div className="fx ac gap8 mt12" style={{ flexWrap: "wrap" }}>
              <span className="fs12 w7 sub">역추적</span>
              <span className="srcchip">질의 “{result.search_text}”</span>
              <span className="farrow">›</span>
              <span className="srcchip">소스 클레임{result.matches[0].human_confirmed ? " · 확인됨" : ""}</span>
              <span className="farrow">›</span>
              <span className="srcchip">{SOURCE_LABEL[result.matches[0].source.type] ?? result.matches[0].source.type} 원본</span>
              {result.matches[0].pages[0] && (<>
                <span className="farrow">›</span>
                <span className="srcchip">위키 · {result.matches[0].pages[0].title}</span>
              </>)}
            </div>
          )}
        </div>
      )}

      {matches.length > 0 && (
        <>
          <div className="fx jb ac mb12" style={{ maxWidth: 860 }}>
            <span className="fs14 w7">소스 클레임 유사도 결과</span>
            <span className="fs12 stone">질의 → 소스클레임 → 원본 → 위키 경로</span>
          </div>
          <div className="fx col gap12" style={{ maxWidth: 860 }}>
            {matches.map((m: any) => (
              <div className="card pad24" key={m.claim_id}>
                <div className="fx jb ac mb4">
                  <div className="fs16 w7">{m.pages[0]?.title ?? "(위키 미연결 클레임)"}</div>
                  <span className="bdg bdg-blue">매칭 {Math.round(m.sim * 100)}%</span>
                </div>
                <div className="fs12 stone mb8">
                  {m.pages[0] ? m.pages[0].folder_path.replaceAll("/", " › ") : `${SOURCE_LABEL[m.source.type] ?? m.source.type} · ${m.source.title}`}
                  {" · "}{m.human_confirmed ? "확인됨" : "소스 클레임"}
                </div>
                <div className="fs14 chc">{m.claim_text}</div>
                {m.pages[0] && (
                  <div className="mt10"><Link href={`/wiki/${m.pages[0].id}`} className="srcchip">위키 페이지 열기</Link></div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

export default function SearchPage() {
  return <Suspense fallback={<section className="scr"><div className="lede">…</div></section>}><SearchInner /></Suspense>;
}

"use client";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import Link from "next/link";
import { api, fmtDate, SOURCE_LABEL } from "@/lib/api";

export default function WikiPage({ params }: { params: { id: string } }) {
  const [page, setPage] = useState<any>(null);
  const [versions, setVersions] = useState<any[] | null>(null);

  useEffect(() => { api(`/api/wiki/pages/${params.id}`).then(setPage).catch(() => {}); }, [params.id]);
  if (!page) return <section className="scr"><div className="lede">불러오는 중…</div></section>;

  const confirmed = page.claims.filter((c: any) => c.human_confirmed).length;
  const unconfirmed = page.claims.length - confirmed;
  const TYPE_LABEL: any = { concept: "개념", entity: "엔티티", topic: "주제" };
  // [[페이지 제목]] 내부 링크 → 검색 링크로 렌더 (위키 운영지침 §2-3)
  const linkedBody = String(page.body).replace(/\[\[([^\]]+)\]\]/g,
    (_m: string, t: string) => `[${t}](/search?q=${encodeURIComponent(t)})`);

  return (
    <section className="scr">
      <div className="kicker">위키 · 페이지</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 32 }}>
        <div>
          <div className="fx ac gap8 mb10">
            <span className="fs14 sub">{page.folder_path.replaceAll("/", " › ")}</span>
            <span className="bdg bdg-ink">{TYPE_LABEL[page.page_type] ?? page.page_type}</span>
            {confirmed > 0 && <span className="bdg bdg-success">확인됨 {confirmed}</span>}
            {page.open_issues.length > 0 && <span className="bdg bdg-attn">린트 {page.open_issues.length}건</span>}
          </div>
          <div className="h1 mb16">{page.title}</div>

          {page.open_issues.length > 0 && (
            <div className="card pad16 mb16" style={{ background: "#fdf3da", border: "none" }}>
              {page.open_issues.map((i: any) => (
                <div key={i.id} className="fs14 chc">
                  <span className={`bdg ${i.type === "contradiction" ? "bdg-crit" : "bdg-attn"}`} style={{ marginRight: 6 }}>
                    {i.type === "contradiction" ? "모순" : "빈틈"}</span>
                  {i.question} — <Link href="/inbox">확인 요청함에서 답변</Link>
                </div>
              ))}
            </div>
          )}

          <div className="md"><ReactMarkdown>{linkedBody}</ReactMarkdown></div>

          {versions && (
            <div className="mt24">
              <div className="fs16 w7 mb10">리비전 이력</div>
              <div className="fx col gap10">
                {versions.map((v) => (
                  <div className="card pad16" key={v.id}>
                    <div className="fx ac gap8 mb6">
                      <span className="bdg bdg-soft">{({ ingest: "인제스트", lint: "린트", answer: "역질문 답변", edit: "수정" } as any)[v.cause] ?? v.cause}</span>
                      <span className="fs12 stone">{fmtDate(v.created_at)}</span>
                    </div>
                    <details><summary className="fs12 stone" style={{ cursor: "pointer" }}>본문 보기</summary>
                      <div className="difins mt6" style={{ background: "var(--soft)", color: "var(--charcoal)" }}>{v.body}</div>
                    </details>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="fx col gap16" style={{ alignSelf: "flex-start" }}>
          <div className="card pad20">
            <div className="fx jb ac mb12">
              <div className="fs14 w7">근거 소스 클레임</div>
              <span className="fs12 stone">확인됨 {confirmed} · 미확인 {unconfirmed}</span>
            </div>
            <div className="fx col gap8">
              {page.claims.map((c: any) => (
                <div key={c.id} className="fx ac gap8"
                  style={{ padding: "10px 12px", background: "var(--soft)", borderRadius: 12, opacity: c.superseded ? 0.55 : 1 }}>
                  {c.human_confirmed ? <span className="bdg bdg-success" style={{ flex: "none" }}>확인됨</span>
                    : c.superseded ? <span className="bdg bdg-soft" style={{ flex: "none" }}>대체됨</span>
                    : <span className="bdg bdg-blue" style={{ flex: "none" }}>{c.claim_type === "rule" ? "소스" : "event"}</span>}
                  <span className="fs13" style={{ flex: 1 }}>{c.text}</span>
                </div>
              ))}
            </div>
            <div className="fx ac gap4 mt12" style={{ flexWrap: "wrap" }}>
              {Array.from(new Set(page.claims.map((c: any) => `${SOURCE_LABEL[c.source.type] ?? c.source.type} · ${c.source.title}`))).slice(0, 5).map((s: any) => (
                <span key={s} className="srcchip">{s}</span>
              ))}
            </div>
            <div className="fs12 stone mt12" style={{ lineHeight: 1.5 }}>
              소스 클레임은 전처리 직후 추출되어 불변으로 저장되며, 역질문으로 확인되면 확인됨(human_confirmed)이 붙습니다.
            </div>
          </div>
          <div className="fx col gap8">
            <button className="btn btn-ghost" style={{ justifyContent: "center" }}
              onClick={() => versions ? setVersions(null)
                : api(`/api/wiki/pages/${page.id}/versions`).then((r) => setVersions(r.versions))}>
              {versions ? "리비전 닫기" : "리비전 보기"}
            </button>
            <div className="fs12 stone" style={{ textAlign: "center" }}>
              {fmtDate(page.updated_at)} 갱신 · 시스템만 갱신(편집 잠금)
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

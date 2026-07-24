"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, fmtDate, SOURCE_LABEL } from "@/lib/api";
import ClaimCard from "@/components/ClaimCard";

const FIELD_LABEL: [string, string][] = [
  ["subject", "주어 (subject)"],
  ["predicate", "속성 (predicate)"],
  ["object", "값 (object)"],
  ["polarity", "긍/부정 (polarity)"],
  ["scope", "적용 범위 (scope)"],
  ["time_scope", "유효 시점 (time_scope)"],
  ["claim_type", "성격 (claim_type)"],
  ["owner", "담당자 (owner)"],
  ["project", "프로젝트 (project)"],
];

export default function ClaimDetailPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const [c, setC] = useState<any>(null);
  const [srcView, setSrcView] = useState<any>(null);

  useEffect(() => { api(`/api/claims/${params.id}`).then(setC).catch(() => {}); }, [params.id]);
  if (!c) return <section className="scr"><div className="lede">불러오는 중…</div></section>;

  async function openSource() {
    if (!c.source) return;
    try { setSrcView(await api(`/api/sources/${c.source.id}`)); } catch {}
  }

  return (
    <section className="scr">
      <div className="kicker">위키 · 클레임 상세</div>
      <div className="fx jb mb16" style={{ alignItems: "flex-end" }}>
        <div className="h1">클레임 #{c.id}</div>
        <div className="fx gap6">
          <Link className="btn btn-primary" href={`/graph?focus=claim:${c.id}`}>관계 그래프</Link>
          <Link className="btn btn-ghost" href="/claims">← 목록</Link>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 24 }}>
        <div className="fx col gap16">
          <ClaimCard claim={c} label={`클레임 #${c.id}`} tone="blue" />

          <div className="card pad24">
            <div className="fs14 w7 mb12">요소 상세</div>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {FIELD_LABEL.map(([k, label]) => (
                  <tr key={k} style={{ borderBottom: "1px solid var(--hairsoft)" }}>
                    <td className="fs13 stone" style={{ padding: "9px 0", width: 180 }}>{label}</td>
                    <td className="fs14" style={{ padding: "9px 0", fontWeight: 600 }}>
                      {k === "polarity" ? (c[k] === "negative" ? "부정 (아니다)" : "긍정 (이다)")
                        : k === "claim_type" ? (c[k] === "rule" ? "rule — 현재값이 하나 (모순 검사 대상)" : "event — 사건 기록 (검사 제외)")
                        : (c[k] ?? <span className="stone" style={{ fontWeight: 400 }}>—</span>)}
                    </td>
                  </tr>
                ))}
                <tr style={{ borderBottom: "1px solid var(--hairsoft)" }}>
                  <td className="fs13 stone" style={{ padding: "9px 0" }}>확신도 (confidence)</td>
                  <td className="fs14" style={{ padding: "9px 0", fontWeight: 600 }}>
                    {Math.round((c.confidence ?? 0) * 100)}% — {c.confidence >= 0.9 ? "공식 규정·확정" : c.confidence >= 0.8 ? "명확한 단정" : c.confidence >= 0.65 ? "계획·예정" : c.confidence >= 0.45 ? "논의·제안" : "전언·추측"}
                  </td>
                </tr>
                <tr style={{ borderBottom: "1px solid var(--hairsoft)" }}>
                  <td className="fs13 stone" style={{ padding: "9px 0" }}>상태</td>
                  <td style={{ padding: "9px 0" }}>
                    {c.human_confirmed && <span className="bdg bdg-success" style={{ marginRight: 6 }}>확인됨 (사람이 확정)</span>}
                    {c.superseded && <span className="bdg bdg-soft" style={{ marginRight: 6 }}>대체됨 (검색·린트 제외)</span>}
                    {!c.human_confirmed && !c.superseded && <span className="bdg bdg-blue">활성 · 미확인</span>}
                  </td>
                </tr>
                <tr>
                  <td className="fs13 stone" style={{ padding: "9px 0" }}>추출 시각</td>
                  <td className="fs14" style={{ padding: "9px 0" }}>{fmtDate(c.created_at)}</td>
                </tr>
              </tbody>
            </table>
            <div className="fs12 stone mt12">검색 문장(rendered_text): {c.rendered_text}</div>
          </div>

          {c.issues?.length > 0 && (
            <div className="card pad24">
              <div className="fs14 w7 mb12">관련 확인 요청</div>
              <div className="fx col gap8">
                {c.issues.map((i: any) => (
                  <div key={i.id} className="fx ac gap8" style={{ padding: "10px 12px", background: "var(--soft)", borderRadius: 12 }}>
                    <span className={`bdg ${i.status === "open" ? "bdg-crit" : "bdg-success"}`} style={{ flex: "none" }}>
                      {i.status === "open" ? "대기" : "답변됨"}</span>
                    <span className="fs13" style={{ flex: 1 }}>{i.question}{i.answer ? ` → ${i.answer}` : ""}</span>
                    {i.status === "open" && <Link className="fs12" href="/inbox" style={{ color: "var(--primary-deep)", fontWeight: 700, flex: "none" }}>답변하기</Link>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="fx col gap16" style={{ alignSelf: "flex-start" }}>
          {c.source && (
            <div className="card pad20">
              <div className="fs14 w7 mb10">원본 소스</div>
              <div className="fs13 mb2" style={{ fontWeight: 600 }}>{c.source.title ?? c.source.path}</div>
              <div className="fs12 stone mb10">{SOURCE_LABEL[c.source.type] ?? c.source.type} · {c.source.path}</div>
              <button className="btn btn-ghost btn-sm" style={{ padding: "6px 14px", fontSize: 12 }} onClick={openSource}>원본 보기</button>
            </div>
          )}

          <div className="card pad20">
            <div className="fs14 w7 mb10">반영된 위키 페이지</div>
            {c.pages?.length ? (
              <div className="fx col gap6">
                {c.pages.map((p: any) => (
                  <Link key={p.id} href={`/wiki/${p.id}`} className="fs13" style={{ color: "var(--primary-deep)", fontWeight: 600 }}>
                    {p.folder_path.replaceAll("/", " › ")} › {p.title}
                  </Link>
                ))}
              </div>
            ) : <div className="fs13 stone">아직 반영된 페이지 없음</div>}
          </div>

          <div className="card pad20">
            <div className="fs14 w7 mb10">유사 클레임</div>
            <div className="fx col gap8">
              {c.similar?.map((s: any) => (
                <div key={s.id} style={{ cursor: "pointer" }} onClick={() => router.push(`/claims/${s.id}`)}>
                  <div className="fx jb ac mb2">
                    <span className="fs12 w7 sub">#{s.id}</span>
                    <span className="fs12 stone">유사도 {Math.round(s.sim * 100)}%</span>
                  </div>
                  <div className="fs13" style={{ padding: "8px 10px", background: "var(--soft)", borderRadius: 10 }}>
                    {s.subject}의 {s.predicate}: <b>{s.object}</b>
                    {s.owner ? <span className="stone"> · 담당 {s.owner}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {srcView && (
        <div onClick={() => setSrcView(null)}
          style={{ position: "fixed", inset: 0, background: "rgba(15,23,32,.45)", zIndex: 90,
            display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div onClick={(e) => e.stopPropagation()} className="card-lg"
            style={{ width: "min(860px, 100%)", maxHeight: "84vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div className="fx jb ac" style={{ padding: "16px 22px", borderBottom: "1px solid var(--hairsoft)" }}>
              <div>
                <div className="fs16 w7">{srcView.title ?? srcView.path}</div>
                <div className="fs12 stone mt2">{srcView.path} · v{srcView.version} · {fmtDate(srcView.ingested_at)} 수집</div>
              </div>
              <button className="iconbtn" style={{ border: "none" }} onClick={() => setSrcView(null)}>
                <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" /></svg>
              </button>
            </div>
            <div style={{ overflow: "auto", padding: "18px 22px" }}>
              <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13.5, lineHeight: 1.7,
                fontFamily: "Pretendard, sans-serif", margin: 0 }}>
                {(() => {
                  const ct = srcView.content ?? {};
                  const fields: string[] = ct.content_field_names ?? [];
                  const body = fields.map((f) => ct[f]).filter(Boolean).join("\n\n");
                  return body || JSON.stringify(ct, null, 2);
                })()}
              </pre>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

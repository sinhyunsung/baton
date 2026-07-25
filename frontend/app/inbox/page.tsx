"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, fmtDate } from "@/lib/api";
import ClaimCard from "@/components/ClaimCard";

export default function InboxPage() {
  const [issues, setIssues] = useState<any[]>([]);
  const [counts, setCounts] = useState<any>({});
  const [answered, setAnswered] = useState<any[]>([]);
  const [sel, setSel] = useState(0);
  const [custom, setCustom] = useState("");

  const load = () => {
    api("/api/lint/issues?status=open").then((r) => { setIssues(r.issues); setCounts(r.open_counts); setSel(0); }).catch(() => {});
    api("/api/lint/issues?status=answered").then((r) => setAnswered(r.issues)).catch(() => {});
  };
  useEffect(load, []);

  async function answer(id: number, value: string) {
    if (!value.trim()) return;
    await api(`/api/lint/issues/${id}/answer`, { method: "POST", body: JSON.stringify({ answer: value }) });
    setCustom(""); load();
  }

  const cur = issues[sel];
  return (
    <section className="scr">
      <div className="kicker">확인 요청함 · 역질문</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">확인 요청함</div>
        <div className="fx gap6">
          <span className="bdg bdg-crit"><i />모순 {counts.contradiction ?? 0}</span>
          <span className="bdg bdg-attn"><i />빈틈 {counts.gap ?? 0}</span>
          <span className="bdg bdg-soft">대기 {issues.length}</span>
        </div>
      </div>
      <div className="lede mb24">
        린트가 찾은 모순·빈틈이 공용 역질문으로 올라옵니다. 별도 로그인이 없어 누구나 답변할 수 있고(담당자는 태그로 표시만),
        답변이 확정되면 소스 클레임에 human_confirmed가 기록되고 위키에 즉시 반영됩니다.
      </div>

      {issues.length === 0 ? (
        <div className="card-lg pad24 chc mb24">대기 중인 확인 요청이 없습니다. <Link href="/lint">린트를 실행</Link>해보세요.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: 24 }}>
          <div className="fx col gap10" style={{ alignSelf: "flex-start" }}>
            {issues.map((it, i) => (
              <div key={it.id} className={`selrow ${i === sel ? "on" : ""}`} onClick={() => setSel(i)}>
                <div className="fx ac gap6 mb6">
                  <span className={`bdg ${it.type === "contradiction" ? "bdg-crit" : it.type === "taxonomy" ? "bdg-ink" : "bdg-attn"}`}>
                    {it.type === "contradiction" ? "모순" : it.type === "taxonomy" ? "분류" : "빈틈"}</span>
                  <span className="bdg bdg-soft">{it.assignee ?? "미지정"}</span>
                </div>
                <div className="fs14 w7">{it.question.length > 44 ? it.question.slice(0, 44) + "…" : it.question}</div>
              </div>
            ))}
          </div>

          {cur && (
            <div className="card-lg pad32">
              <div className="fx jb ac mb10">
                <div className="fx ac gap6">
                  <span className={`bdg ${cur.type === "contradiction" ? "bdg-crit" : cur.type === "taxonomy" ? "bdg-ink" : "bdg-attn"}`}>
                    {cur.type === "contradiction" ? "모순" : cur.type === "taxonomy" ? "분류" : "빈틈"}</span>
                  {cur.page && <span className="bdg bdg-soft">위키 · {cur.page.title}</span>}
                </div>
                <span className="bdg bdg-soft">담당 {cur.assignee ?? "미지정"}</span>
              </div>
              <div className="fs24 w5 disp mb8">{cur.question}</div>
              <div className="fs14 chc mb20" style={{ lineHeight: 1.5 }}>
                {cur.detail ?? "확인이 필요합니다."} 답변이 확정되면 소스 클레임에 human_confirmed가 기록됩니다.
              </div>

              {cur.claims?.length >= 2 ? (
                /* 모순: 충돌하는 두 클레임을 나란히 전체 비교 */
                <div className="two mb24" style={{ gap: 14 }}>
                  <ClaimCard claim={cur.claims[0]} label="클레임 A" tone="blue" />
                  <ClaimCard claim={cur.claims[1]} label="클레임 B" tone="red" />
                </div>
              ) : cur.claims?.length === 1 ? (
                /* 위키↔소스 표류: 소스 클레임 + 위키 기재 값 */
                <div className="two mb24" style={{ gap: 14 }}>
                  <ClaimCard claim={cur.claims[0]} label="소스 클레임" tone="blue" />
                  {cur.options?.filter((o: string) => o !== cur.claims[0]?.object).slice(0, 1).map((o: string) => (
                    <div key={o} style={{ borderRadius: 16, padding: "16px 18px", background: "#fdeef0", borderLeft: "3px solid #d64550" }}>
                      <div className="fs12 w7 sub mb8">위키 기재 값</div>
                      <div className="fs17 w7">{o}</div>
                    </div>
                  ))}
                </div>
              ) : cur.type === "gap" ? (
                <div className="mb24" style={{ borderRadius: 16, padding: "16px 18px", background: "#fdeef0", borderLeft: "3px solid #d64550", maxWidth: 480 }}>
                  <div className="fs12 w7 sub mb8">현재 상태</div>
                  <div className="fs16 w7 mb4">정보 없음</div>
                  <div className="fs12 stone">어떤 소스에도 존재하지 않아 답변으로 채워야 합니다</div>
                </div>
              ) : null}

              <div className="fx gap10" style={{ flexWrap: "wrap" }}>
                {(cur.options ?? []).map((o: string) => (
                  <button key={o} className="btn btn-cta" onClick={() => answer(cur.id, o)}>“{o}” 로 확정</button>
                ))}
              </div>
              <div className="fx gap8 mt12" style={{ maxWidth: 520 }}>
                <input className="inp" placeholder="다른 값으로 답변" value={custom}
                  onChange={(e) => setCustom(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && answer(cur.id, custom)} />
                <button className="btn btn-ghost" onClick={() => answer(cur.id, custom)}>답변</button>
              </div>
            </div>
          )}
        </div>
      )}

      {answered.length > 0 && (
        <div className="mt24">
          <div className="fs16 w7 mb10">답변 완료</div>
          <div className="fx col gap8">
            {answered.map((it) => (
              <div className="card pad16" key={it.id} style={{ opacity: 0.75 }}>
                <span className="bdg bdg-success" style={{ marginRight: 8 }}>답변됨</span>
                <span className="fs14">{it.question}</span>
                <div className="fs12 stone mt4">
                  → {it.answer} · {fmtDate(it.created_at)} · 위키에 반영됨
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const CAUSE: Record<string, [string, string]> = {
  ingest: ["소스 클레임", "bdg-blue"], lint: ["린트", "bdg-attn"], answer: ["확인됨", "bdg-success"],
};

export default function ProposalsPage() {
  const [props, setProps] = useState<any[]>([]);
  const [sel, setSel] = useState(0);
  const [editBody, setEditBody] = useState<string | null>(null);

  const load = () => { api("/api/proposals?status=pending").then((r) => { setProps(r.proposals); setSel(0); setEditBody(null); }).catch(() => {}); };
  useEffect(load, []);

  async function decide(id: number, action: "approve" | "reject", edited?: string | null) {
    await api(`/api/proposals/${id}/${action}`, {
      method: "POST",
      body: JSON.stringify(action === "approve" ? { edited_body: edited ?? null } : {}),
    });
    load();
  }

  const cur = props[sel];
  return (
    <section className="scr">
      <div className="kicker">데이터 · 갱신안</div>
      <div className="fx jb mb24" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="h1 mb8">위키 갱신안 검토</div>
          <div className="lede">AI가 만든 갱신안을 코드 리뷰처럼 승인하세요. 승인해야 위키에 반영되고 버전이 남습니다.</div>
        </div>
        <span className="bdg bdg-warn">대기 {props.length}건</span>
      </div>

      {props.length === 0 ? (
        <div className="card-lg pad24 chc">대기 중인 갱신안이 없습니다.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: 24 }}>
          <div className="fx col gap10" style={{ alignSelf: "flex-start" }}>
            {props.map((p, i) => {
              const [label, cls] = CAUSE[p.cause] ?? [p.cause, "bdg-soft"];
              return (
                <div key={p.id} className={`selrow ${i === sel ? "on" : ""}`} onClick={() => { setSel(i); setEditBody(null); }}>
                  <div className="fx jb ac mb6">
                    <div className="fs14 w7">{p.title}</div>
                    <span className={`bdg ${cls}`}>{label}</span>
                  </div>
                  <div className="fs12 stone">{p.folder_path} · 근거 {p.evidence_claim_ids?.length ?? 0}개</div>
                </div>
              );
            })}
          </div>

          {cur && (
            <div className="card-lg pad32">
              <div className="fx jb ac mb4">
                <div className="fs24 w5 disp">{cur.title}</div>
                <span className={`bdg ${cur.page_id ? "bdg-attn" : "bdg-success"}`}>{cur.page_id ? "페이지 수정" : "신규 페이지"}</span>
                <span className="bdg bdg-ink" style={{ marginLeft: 6 }}>{({ concept: "개념", entity: "엔티티", topic: "주제" } as any)[cur.page_type] ?? "개념"}</span>
              </div>
              <div className="fs13 stone mb20">
                영향 범위 {cur.folder_path.replaceAll("/", " › ")} · 근거 클레임 {cur.evidence_claim_ids?.length ?? 0}개 ·{" "}
                {(CAUSE[cur.cause] ?? [cur.cause])[0]} 기반
              </div>

              {cur.old_body && (
                <div className="difdel mb8">− {cur.old_body}</div>
              )}
              {editBody !== null ? (
                <textarea className="inp mb24" rows={16} value={editBody} onChange={(e) => setEditBody(e.target.value)} />
              ) : (
                <div className="difins mb24">+ {cur.new_body}</div>
              )}

              <div className="fx gap10">
                {editBody !== null ? (
                  <>
                    <button className="btn btn-cta" onClick={() => decide(cur.id, "approve", editBody)}>수정 내용으로 승인</button>
                    <button className="btn btn-ghost" onClick={() => setEditBody(null)}>편집 취소</button>
                  </>
                ) : (
                  <>
                    <button className="btn btn-cta" onClick={() => decide(cur.id, "approve")}>갱신 승인</button>
                    <button className="btn btn-ghost" onClick={() => setEditBody(cur.new_body)}>수정 후 승인</button>
                    <button className="btn btn-ghost" style={{ color: "var(--crit)" }} onClick={() => decide(cur.id, "reject")}>반려</button>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

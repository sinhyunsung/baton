"use client";
import { useEffect, useRef, useState } from "react";
import { api, fmtDate } from "@/lib/api";

/** 원본 소스 뷰어 모달 — sourceId를 받아 원문(본문 필드)과 메타데이터를 보여준다. */
export default function SourceViewer({ sourceId, chunkIdx, onClose }: { sourceId: number; chunkIdx?: number | null; onClose: () => void }) {
  const [src, setSrc] = useState<any>(null);
  const hlRef = useRef<HTMLElement>(null);
  useEffect(() => { api(`/api/sources/${sourceId}`).then(setSrc).catch(() => {}); }, [sourceId]);
  useEffect(() => {
    if (src && hlRef.current) setTimeout(() => hlRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 80);
  }, [src]);

  return (
    <div onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(15,23,32,.45)", zIndex: 90,
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div onClick={(e) => e.stopPropagation()} className="card-lg"
        style={{ width: "min(860px, 100%)", maxHeight: "84vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="fx jb ac" style={{ padding: "16px 22px", borderBottom: "1px solid var(--hairsoft)" }}>
          <div>
            <div className="fs16 w7">{src ? (src.title ?? src.path) : "불러오는 중…"}</div>
            {src && (
              <div className="fs12 stone mt2">
                {src.path} · v{src.version} · {fmtDate(src.ingested_at)} 수집 · 원본 불변(sha256 {String(src.sha256).slice(0, 10)}…)
              </div>
            )}
          </div>
          <button className="iconbtn" style={{ border: "none" }} onClick={onClose}>
            <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" /></svg>
          </button>
        </div>
        {src && (
          <div style={{ overflow: "auto", padding: "18px 22px" }}>
            {chunkIdx != null && src.chunks?.length > 0 && (
              <div className="fs12 stone mb8">파란 글씨 = 이 클레임이 추출된 부분 (청크 {chunkIdx}/{src.chunks.length})</div>
            )}
            <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13.5, lineHeight: 1.7,
              fontFamily: "Pretendard, sans-serif", margin: 0 }}>
              {(() => {
                // 청크가 있으면 원문을 청크 나열로 재구성해, 해당 청크만 파란 글씨로 인라인 강조
                if (src.chunks?.length > 0 && chunkIdx != null && src.chunks[chunkIdx - 1] != null) {
                  return src.chunks.map((ch: string, i: number) =>
                    i === chunkIdx - 1
                      ? <span key={i} ref={hlRef as any} style={{ color: "#0064E0", fontWeight: 600 }}>{(i > 0 ? "\n\n" : "") + ch}</span>
                      : <span key={i}>{(i > 0 ? "\n\n" : "") + ch}</span>
                  );
                }
                const c = src.content ?? {};
                const fields: string[] = c.content_field_names ?? [];
                const body = fields.map((f) => c[f]).filter(Boolean).join("\n\n");
                return body || JSON.stringify(c, null, 2);
              })()}
            </pre>
            {(() => {
              const c = src.content ?? {};
              const hide = new Set([...(c.content_field_names ?? []), "content_field_names", "title_field_name", "dataset_doc_uuid", "file_ext"]);
              const meta = Object.entries(c).filter(([k]) => !hide.has(k));
              return meta.length > 0 && (
                <div className="mt16" style={{ borderTop: "1px solid var(--hairsoft)", paddingTop: 12 }}>
                  <div className="fs12 w7 sub mb6">메타데이터</div>
                  {meta.map(([k, v]) => (
                    <div key={k} className="fs12 stone" style={{ lineHeight: 1.7 }}>
                      <b>{k}</b>: {typeof v === "object" ? JSON.stringify(v) : String(v)}
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        )}
      </div>
    </div>
  );
}

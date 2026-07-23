"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { api, sse } from "@/lib/api";

type Src = { claim_text: string; sim: number; human_confirmed: boolean;
  source_type: string; source_title: string; pages: { id: number; title: string }[] };
type Msg = { role: "me" | "ai"; text: string; sources?: Src[];
  noEvidence?: { message: string; question: string; sent?: boolean } };

const ChatIcon = () => (
  <svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-4 4V5Z" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

export default function ChatWidget() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const restored = useRef(false);

  // 전체 리로드(새로고침·외부 링크)에도 대화가 유지되도록 세션에 보존/복원
  // (SSR 하이드레이션 불일치를 피하려고 마운트 후 복원)
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem("baton_chat");
      if (raw) {
        const d = JSON.parse(raw);
        setMsgs(d.msgs ?? []);
        setOpen(!!d.open);
      }
    } catch {}
    restored.current = true;
  }, []);
  useEffect(() => {
    if (!restored.current) return;
    try { sessionStorage.setItem("baton_chat", JSON.stringify({ msgs: msgs.slice(-40), open })); } catch {}
  }, [msgs, open]);
  const scroll = () => setTimeout(() => bodyRef.current?.scrollTo(0, 1e6), 30);

  async function send() {
    const q = input.trim();
    if (!q || busy) return;
    setInput(""); setBusy(true);
    setMsgs((m) => [...m, { role: "me", text: q }, { role: "ai", text: "" }]);
    scroll();
    try {
      await sse("/api/chat", { question: q }, (event, data) => {
        setMsgs((m) => {
          const out = [...m];
          const last = { ...out[out.length - 1] };
          if (event === "meta") last.sources = JSON.parse(data).sources;
          else if (event === "token") last.text += data;
          else if (event === "no_evidence")
            last.noEvidence = { message: JSON.parse(data).message, question: q };
          out[out.length - 1] = last;
          return out;
        });
        scroll();
      });
    } catch {
      setMsgs((m) => [...m.slice(0, -1), { role: "ai", text: "오류가 발생했습니다." }]);
    }
    setBusy(false); scroll();
  }

  async function backquestion(i: number) {
    const q = msgs[i].noEvidence?.question;
    if (!q) return;
    await api("/api/chat/backquestion", { method: "POST", body: JSON.stringify({ question: q }) });
    setMsgs((m) => m.map((msg, j) => j === i ? { ...msg, noEvidence: { ...msg.noEvidence!, sent: true } } : msg));
  }

  return (
    <>
      <div className={`chatpanel ${open ? "open" : ""}`}>
        <div className="fx ac jb" style={{ padding: "14px 18px", borderBottom: "1px solid var(--hairsoft)" }}>
          <div className="fx ac gap10">
            <div style={{ width: 30, height: 30, borderRadius: "50%", background: "var(--primary)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2"><path d="M4 5h16v11H9l-4 4V5Z" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </div>
            <div>
              <div className="fs14 w7">Baton 챗봇</div>
              <div className="fs12 stone">위키 근거로 답해요</div>
            </div>
          </div>
          <button className="iconbtn" style={{ border: "none" }} onClick={() => setOpen(false)}>
            <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" /></svg>
          </button>
        </div>
        <div className="fx col gap12" ref={bodyRef} style={{ flex: 1, overflow: "auto", padding: 16, background: "var(--canvas)" }}>
          {msgs.length === 0 && <div className="bubble ai">위키에 무엇이든 물어보세요.<br />예) 온보딩 총괄 담당자가 누구야?</div>}
          {msgs.map((m, i) =>
            m.role === "me" ? (
              <div key={i} className="bubble me">{m.text}</div>
            ) : (
              <div key={i} style={{ alignSelf: "flex-start", maxWidth: "90%" }}>
                <div className="bubble ai">
                  {m.noEvidence ? m.noEvidence.message :
                    <div className="md" style={{ fontSize: 13.5 }}><ReactMarkdown>{m.text || (busy && i === msgs.length - 1 ? "…" : "")}</ReactMarkdown></div>}
                </div>
                {m.noEvidence && !m.noEvidence.sent && (
                  <div className="fx ac gap6 mt6" style={{ paddingLeft: 4 }}>
                    <button className="btn btn-cta btn-sm" style={{ padding: "6px 14px", fontSize: 12 }} onClick={() => backquestion(i)}>역질문 생성</button>
                    <button className="btn btn-ghost btn-sm" style={{ padding: "5px 13px", fontSize: 12 }}
                      onClick={() => setMsgs((all) => all.map((msg, j) => j === i ? { ...msg, noEvidence: undefined, text: "건너뛰었습니다." } : msg))}>건너뛰기</button>
                  </div>
                )}
                {m.noEvidence?.sent && (
                  <div className="fx ac gap4 mt4" style={{ paddingLeft: 4 }}>
                    <span className="bdg bdg-success">역질문이 확인 요청함에 게시됨</span>
                  </div>
                )}
                {m.sources && m.sources.length > 0 && (
                  <div className="fx ac gap4 mt4" style={{ paddingLeft: 4, flexWrap: "wrap" }}>
                    {/* 여러 클레임이 같은 페이지에 연결될 수 있으므로 페이지(제목) 기준으로 중복 제거 */}
                    {m.sources.reduce((acc: { label: string; pageId?: number; confirmed: boolean; n: number }[], s) => {
                      const label = s.pages[0]?.title ?? s.source_title;
                      const ex = acc.find((u) => u.label === label);
                      if (ex) { ex.confirmed = ex.confirmed || s.human_confirmed; ex.n += 1; }
                      else acc.push({ label, pageId: s.pages[0]?.id, confirmed: s.human_confirmed, n: 1 });
                      return acc;
                    }, []).slice(0, 3).map((u, j) => (
                      <span key={j} className="srcchip"
                        onClick={() => u.pageId && router.push(`/wiki/${u.pageId}`)}>
                        {u.label}{u.n > 1 ? ` ·근거 ${u.n}` : ""}{u.confirmed ? " ✓" : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )
          )}
        </div>
        <div className="fx gap8" style={{ padding: "12px 14px", borderTop: "1px solid var(--hairsoft)" }}>
          <input className="inp" style={{ height: 38, fontSize: 13, padding: "8px 12px" }}
            placeholder="위키에 무엇이든 물어보세요" value={input} disabled={busy}
            onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <button className="btn btn-cta btn-sm" onClick={send} disabled={busy}>질문 보내기</button>
        </div>
      </div>
      <button className="chatfab" onClick={() => setOpen(!open)} title="Baton 챗봇"><ChatIcon /></button>
    </>
  );
}

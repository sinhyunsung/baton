"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

const BRAND: Record<string, { name: string; initial: string; color: string; desc: string }> = {
  slack: { name: "Slack", initial: "S", color: "#4A154B", desc: "공개 채널" },
  google_drive: { name: "Google Drive", initial: "D", color: "#1a73e8", desc: "문서·표" },
  gmail: { name: "Gmail", initial: "M", color: "#ea4335", desc: "사내 메일" },
  jira: { name: "Jira", initial: "J", color: "#0052cc", desc: "이슈·코멘트" },
  github: { name: "GitHub", initial: "G", color: "#1a1e22", desc: "PR·마크다운" },
  confluence: { name: "Confluence", initial: "C", color: "#172b4d", desc: "사내 위키" },
  fireflies: { name: "Fireflies", initial: "F", color: "#8b5cf6", desc: "회의록" },
  local: { name: "로컬 파일", initial: "L", color: "#5d6c7b", desc: "txt·md·json" },
};

const OK_EXT = [".json", ".txt", ".md", ".markdown"];

export default function AdminPage() {
  const [summary, setSummary] = useState<any>(null);
  const [picked, setPicked] = useState<File[]>([]);
  const [limit, setLimit] = useState(10);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const dirRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api("/api/sources/summary").then(setSummary).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);

  function onPick(list: FileList | null) {
    if (!list) return;
    const files = Array.from(list).filter((f) =>
      OK_EXT.some((e) => f.name.toLowerCase().endsWith(e)) && f.name !== "agents.md");
    setPicked(files);
    setMsg(files.length ? `${files.length}개 파일 선택됨 (json·txt·md만 인제스트)` : "");
    setErr(files.length ? "" : "인제스트 가능한 파일(json·txt·md)이 없습니다");
  }

  async function upload() {
    if (!picked.length || uploading) return;
    setUploading(true); setErr(""); setMsg(`업로드 중… (${picked.length}개 파일)`);
    try {
      const fd = new FormData();
      for (const f of picked) {
        fd.append("files", f);
        fd.append("paths", (f as any).webkitRelativePath || f.name);
      }
      if (limit) fd.append("limit", String(limit));
      const res = await fetch("/api/ingest/upload", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      const r = await res.json();
      setMsg(`업로드 완료 ${r.uploaded}건 → 인제스트 시작 (신규 ${limit}건 처리). 인제스트 현황에서 진행을 확인하세요.`);
      setPicked([]);
      if (dirRef.current) dirRef.current.value = "";
      if (fileRef.current) fileRef.current.value = "";
    } catch (e: any) {
      setErr(e.message?.includes("409") ? "다른 작업이 실행 중입니다" : "업로드에 실패했습니다");
    }
    setUploading(false);
    load();
  }

  async function resetAll() {
    if (!window.confirm("정말 초기화할까요?\n원본·클레임·위키·확인요청이 전부 삭제됩니다. (되돌릴 수 없음)")) return;
    if (!window.confirm("마지막 확인입니다. 모든 데이터를 삭제합니다.")) return;
    setErr(""); setMsg("");
    try {
      await api("/api/admin/reset", { method: "POST" });
      setMsg("데이터가 초기화되었습니다.");
    } catch { setErr("작업 실행 중에는 리셋할 수 없습니다"); }
    load();
  }

  const running = summary?.job_running;
  const collected: Record<string, number> = summary?.collected ?? {};
  const types = Object.keys(collected);

  return (
    <section className="scr">
      <div className="kicker">관리 · 데이터 소스</div>
      <div className="h1 mb12">데이터 소스</div>
      <div className="lede mb24">
        내 컴퓨터의 파일을 올려 인제스트합니다. <b>폴더째</b> 올리면 하위 구조가 보존되고
        1단계 하위 폴더명이 소스 타입이 됩니다. <b>json · txt · md</b> 파일을 지원합니다.
      </div>

      <div className="card-lg pad24 mb24">
        <div className="fs14 w7 mb12">파일 업로드</div>
        <input ref={dirRef} type="file" multiple style={{ display: "none" }}
          // @ts-expect-error 비표준 속성 (폴더 업로드)
          webkitdirectory="" directory=""
          onChange={(e) => onPick(e.target.files)} />
        <input ref={fileRef} type="file" multiple accept=".json,.txt,.md,.markdown" style={{ display: "none" }}
          onChange={(e) => onPick(e.target.files)} />
        <div className="fx gap8 mb12" style={{ flexWrap: "wrap" }}>
          <button className="btn btn-primary" onClick={() => dirRef.current?.click()} disabled={uploading}>📁 폴더 선택</button>
          <button className="btn btn-ghost" onClick={() => fileRef.current?.click()} disabled={uploading}>파일 선택</button>
          <div style={{ flex: 1 }} />
          <span className="fs12 stone">처리 문서 수</span>
          <input className="inp" type="number" min={1} value={limit} style={{ width: 84, height: 40 }}
            onChange={(e) => setLimit(parseInt(e.target.value || "1"))} />
          <button className="btn btn-cta" onClick={upload} disabled={!picked.length || uploading || !!running}>
            {uploading ? "업로드 중…" : running === "ingest" ? "인제스트 실행 중…" : `업로드 & 인제스트${picked.length ? ` (${picked.length})` : ""}`}
          </button>
        </div>
        {(msg || err) && (
          <div className="fs13" style={{ color: err ? "var(--crit)" : "var(--primary-deep)", fontWeight: 700 }}>
            {err || msg}
          </div>
        )}
        <div className="fs12 stone mt10">
          예: 데이터셋 폴더(sources)를 통째로 올리면 slack/jira 등 타입별로 수집됩니다. 개별 txt·md 문서만 올려도 됩니다.
        </div>
      </div>

      <div className="fx jb ac mb12">
        <span className="fs14 w7">수집 현황 (소스 타입별)</span>
        <span className="fs12 stone">전체 {summary?.total_collected ?? 0}건 · sha256 중복 제외</span>
      </div>
      {types.length === 0 ? (
        <div className="card pad24 chc mb24">아직 수집된 문서가 없습니다. 위에서 폴더나 파일을 올려 인제스트를 시작하세요.</div>
      ) : (
        <div className="grid4">
          {types.map((t) => {
            const b = BRAND[t] ?? { name: t, initial: t[0]?.toUpperCase() ?? "?", color: "#5d6c7b", desc: "업로드 폴더" };
            return (
              <div className="card-lg pad24" key={t}>
                <div className="fx jb ac mb14">
                  <div style={{ width: 40, height: 40, borderRadius: 12, background: b.color, color: "#fff",
                    display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700 }}>{b.initial}</div>
                  <span className="bdg bdg-success">수집됨</span>
                </div>
                <div className="fs18 w7 disp mb4">{b.name}</div>
                <div className="fs13 stone">{collected[t]}건 · {b.desc}</div>
              </div>
            );
          })}
        </div>
      )}

      <div className="card pad24 mt24" style={{ border: "1px solid #f3c9c9", borderRadius: 24 }}>
        <div className="fx jb ac">
          <div>
            <div className="fs16 w7 mb4 red">데이터 리셋</div>
            <div className="fs13 stone">원본·클레임·위키·확인요청을 전부 삭제하고 처음부터 시작합니다. 폴더 설정은 유지됩니다.</div>
          </div>
          <button className="btn" style={{ background: "var(--crit)", color: "#fff" }} onClick={resetAll} disabled={!!running}>전체 초기화</button>
        </div>
      </div>

      <div className="card pad24 mt24" style={{ background: "var(--ink-deep)", border: "none", color: "#fff", borderRadius: 24 }}>
        <div className="fs16 w7 mb4">수집 원칙</div>
        <div className="fs14" style={{ opacity: 0.7 }}>
          동일 해시(sha256)는 스킵, 같은 경로·다른 해시는 새 버전으로 저장. 원본은 불변 보존되며 전처리·교정은 사본에만 적용됩니다.
          업로드 파일은 서버 스테이징에 저장된 후 수집 → 클레임 추출 → 위키 반영 순으로 처리됩니다.
        </div>
      </div>
    </section>
  );
}

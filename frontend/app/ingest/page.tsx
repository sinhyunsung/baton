"use client";
import { useEffect, useState } from "react";
import { api, SOURCE_LABEL } from "@/lib/api";

// 파이프라인 5단계: 수집 → 원본 저장 → 조각 분해 → 클레임 추출 → 위키 반영
const STAGE_LABELS = ["수집", "원본 저장", "조각 분해", "클레임 추출", "위키 반영"];
// status → 완료된 칸 수 (collected = 수집+원본저장 완료)
const DONE_COUNT: Record<string, number> = { collected: 2, preprocessed: 3, claimed: 4, wiki_done: 5 };
const STATUS_LABEL: Record<string, string> = {
  collected: "조각 분해", preprocessed: "클레임 추출", claimed: "위키 반영", wiki_done: "완료", failed: "실패",
};

function Track({ status, failedStage }: { status: string; failedStage?: string | null }) {
  const done = status === "failed"
    ? (DONE_COUNT[failedStage ?? "collected"] ?? 2)
    : (DONE_COUNT[status] ?? 1);
  return (
    <div className="prog">
      <div className="progtrack">
        {[0, 1, 2, 3, 4].map((i) => {
          const cls = i < done ? "done"
            : status === "failed" && i === done ? "fail"
            : status !== "wiki_done" && i === done ? "cur" : "";
          return <i key={i} className={cls} />;
        })}
      </div>
      <div className="progmeta">
        {status === "wiki_done" ? <><b>5/5</b> · 위키 반영까지 완료</>
          : status === "failed" ? <><b>{done + 1}단계({STAGE_LABELS[done]})</b>에서 실패</>
          : <><b>{done}/5</b> · {STAGE_LABELS[done]} 진행 중</>}
      </div>
    </div>
  );
}

export default function IngestPage() {
  const [status, setStatus] = useState<any>(null);
  const load = () => api("/api/ingest/status").then(setStatus).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 2500); return () => clearInterval(t); }, []);

  const by = status?.by_status ?? {};
  const running = status?.job_running === "ingest";
  const prog = status?.job_progress ?? {};
  const target = prog.target ?? prog.scanned ?? 0;
  const pct = target > 0 ? Math.min(100, Math.round(((prog.processed ?? 0) / target) * 100)) : 0;

  return (
    <section className="scr">
      <div className="kicker">데이터 · 인제스트</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">인제스트 파이프라인</div>
        <div className="fx gap6">
          <span className="bdg bdg-soft">누적 완료 {by.wiki_done ?? 0}건</span>
          {running && <span className="bdg bdg-blue"><i />실행 중</span>}
          {(by.failed ?? 0) > 0 && <span className="bdg bdg-crit">실패 {by.failed}</span>}
        </div>
      </div>
      <div className="lede mb24">
        수집 → 원본 불변 저장(sha256·버전) → 조각 분해·텍스트화 → 클레임 추출 → 위키 반영.
        표는 event 클레임으로 분기되어 모순 검사에서 제외됩니다.
      </div>

      {(running || prog.processed > 0) && (
        <div className="card-lg pad24 mb20" style={{ background: running ? "rgba(0,145,255,.06)" : undefined }}>
          <div className="fx jb ac mb10">
            <div className="fs14 w7">{running ? "이번 배치 진행 중" : "마지막 배치 결과"}</div>
            {running && prog.current && <span className="fs12 stone">처리 중: {prog.current}</span>}
          </div>
          <div className="stat-live fx gap16 mb12" style={{ flexWrap: "wrap" }}>
            <div><div className="fs24 disp w5">{prog.new ?? 0}</div><div className="fs12 stone">들어온 문서{prog.target ? ` / 목표 ${prog.target}` : ""}</div></div>
            <div><div className="fs24 disp w5">{prog.processed ?? 0}</div><div className="fs12 stone">처리 완료</div></div>
            <div><div className="fs24 disp w5">{prog.claims ?? 0}</div><div className="fs12 stone">추출 클레임</div></div>
            <div><div className="fs24 disp w5" style={{ color: (prog.failed ?? 0) > 0 ? "var(--crit)" : undefined }}>{prog.failed ?? 0}</div><div className="fs12 stone">실패</div></div>
            <div><div className="fs24 disp w5">{prog.skipped ?? 0}</div><div className="fs12 stone">중복 스킵</div></div>
          </div>
          <div style={{ height: 8, borderRadius: 99, background: "var(--hairsoft)", overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", borderRadius: 99,
              background: running ? "var(--primary)" : "var(--success)", transition: "width .6s" }} />
          </div>
          <div className="fs12 stone mt6">{pct}% ({prog.processed ?? 0}/{target || "?"})</div>
        </div>
      )}

      <div className="card mb20" style={{ overflow: "hidden" }}>
        <table className="tbl">
          <thead><tr><th>소스</th><th>단계</th><th style={{ width: 260 }}>진행</th><th style={{ width: 120, textAlign: "right" }}>클레임</th></tr></thead>
          <tbody>
            {(status?.recent ?? []).map((s: any) => (
              <tr key={s.id}>
                <td>
                  <div className="w7">{SOURCE_LABEL[s.type] ?? s.type} · {s.title}</div>
                  <div className="fs12 stone mt4">{s.error ?? s.path}</div>
                </td>
                <td>
                  {s.status === "failed" ? <span className="bdg bdg-crit"><i />실패</span>
                    : s.status === "wiki_done" ? <span className="bdg bdg-success">완료</span>
                    : <span className="bdg bdg-blue"><i />{STATUS_LABEL[s.status]}</span>}
                </td>
                <td><Track status={s.status} failedStage={s.failed_stage} /></td>
                <td style={{ textAlign: "right" }}>
                  {s.status === "failed"
                    ? <button className="btn btn-ghost btn-sm" style={{ padding: "6px 14px" }}
                        onClick={() => api("/api/ingest/retry", { method: "POST" }).then(load)}>재시도</button>
                    : <span className="sub fs13">{s.claims}개</span>}
                </td>
              </tr>
            ))}
            {(!status?.recent || status.recent.length === 0) && (
              <tr><td colSpan={4} className="stone">아직 인제스트된 문서가 없습니다. 데이터 소스에서 파일을 올려 시작하세요.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="two">
        <div style={{ borderRadius: 24, padding: 24, background: "rgba(0,145,255,.07)" }}>
          <div className="fx ac gap8 mb10">
            <span className="bdg bdg-blue">rule 클레임</span>
            <span className="fs12 stone">단일값 사실 · 모순 검사 대상</span>
          </div>
          <div className="fs14 mb6">“규정·담당자·한도·기한처럼 값이 하나로 정해지는 사실”</div>
          <div className="fs14 stone">총 소스 클레임 {status?.total_claims ?? 0}개 저장됨</div>
        </div>
        <div style={{ borderRadius: 24, padding: 24, background: "var(--soft)" }}>
          <div className="fx ac gap8 mb10">
            <span className="bdg bdg-soft" style={{ background: "#fff" }}>event 클레임</span>
            <span className="fs12 stone">표 · 원시기록 · 모순 검사 제외</span>
          </div>
          <div className="fs14 mb6">줄마다 쌓이는 개별 사건·표 데이터</div>
          <div className="fs14 stone">실행 시 실시간 조회로만 사용</div>
        </div>
      </div>
    </section>
  );
}

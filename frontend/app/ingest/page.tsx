"use client";
import { useEffect, useState } from "react";
import { api, SOURCE_LABEL } from "@/lib/api";

const STAGES = ["collected", "preprocessed", "claimed", "wiki_done"];
const STAGE_LABEL: Record<string, string> = {
  collected: "원본 저장", preprocessed: "조각 분해", claimed: "클레임 추출", wiki_done: "완료", failed: "실패",
};

function Track({ status }: { status: string }) {
  const idx = status === "failed" ? 2 : STAGES.indexOf(status);
  return (
    <div className="prog">
      <div className="progtrack">
        {[0, 1, 2, 3, 4].map((i) => {
          const cls = status === "failed" && i === idx + 1 ? "fail"
            : i <= idx ? "done" : status !== "wiki_done" && i === idx + 1 ? "cur" : "";
          return <i key={i} className={cls} />;
        })}
      </div>
      <div className="progmeta">
        {status === "failed" ? <><b>{idx + 2}단계</b>에서 멈춤</>
          : status === "wiki_done" ? <><b>5/5</b> · 소스 클레임 저장됨</>
          : <><b>{idx + 2}/5</b> · {STAGE_LABEL[status]} 진행 중</>}
      </div>
    </div>
  );
}

export default function IngestPage() {
  const [status, setStatus] = useState<any>(null);
  const load = () => api("/api/ingest/status").then(setStatus).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, []);

  const by = status?.by_status ?? {};
  const running = status?.job_running === "ingest";
  const ruleish = status?.total_claims ?? 0;

  return (
    <section className="scr">
      <div className="kicker">데이터 · 인제스트</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">인제스트 파이프라인</div>
        <div className="fx gap6">
          <span className="bdg bdg-soft">완료 {by.wiki_done ?? 0}건</span>
          {running && <span className="bdg bdg-blue"><i />진행 중</span>}
          {(by.failed ?? 0) > 0 && <span className="bdg bdg-crit">실패 {by.failed}</span>}
        </div>
      </div>
      <div className="lede mb24">
        수집 → 원본 불변 저장(sha256·버전) → 조각 분해·텍스트화 → 클레임 추출 → 소스 클레임 저장.
        표는 event 클레임으로 분기되어 모순 검사에서 제외됩니다.
      </div>

      <div className="card mb20" style={{ overflow: "hidden" }}>
        <table className="tbl">
          <thead><tr><th>소스</th><th>단계</th><th style={{ width: 240 }}>진행</th><th style={{ width: 120, textAlign: "right" }}>클레임</th></tr></thead>
          <tbody>
            {(status?.recent ?? []).map((s: any) => (
              <tr key={s.id}>
                <td>
                  <div className="w7">{SOURCE_LABEL[s.type] ?? s.type} · {s.title}</div>
                  <div className="fs12 stone mt4">{s.error ?? s.path}</div>
                </td>
                <td>
                  {s.status === "failed" ? <span className="bdg bdg-crit"><i />{STAGE_LABEL[s.status]}</span>
                    : s.status === "wiki_done" ? <span className="bdg bdg-success">완료</span>
                    : <span className="bdg bdg-blue">{STAGE_LABEL[s.status]}</span>}
                </td>
                <td><Track status={s.status} /></td>
                <td style={{ textAlign: "right" }}>
                  {s.status === "failed"
                    ? <button className="btn btn-ghost btn-sm" style={{ padding: "6px 14px" }}
                        onClick={() => api("/api/ingest/retry", { method: "POST" }).then(load)}>재시도</button>
                    : <span className="sub fs13">{s.claims}개</span>}
                </td>
              </tr>
            ))}
            {(!status?.recent || status.recent.length === 0) && (
              <tr><td colSpan={4} className="stone">아직 인제스트된 문서가 없습니다. 데이터 소스에서 재스캔을 실행하세요.</td></tr>
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
          <div className="fs14 stone">총 소스 클레임 {ruleish}개 저장됨</div>
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

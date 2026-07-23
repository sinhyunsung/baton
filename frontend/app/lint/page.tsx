"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, fmtDate } from "@/lib/api";

export default function LintPage() {
  const [status, setStatus] = useState<any>(null);
  const router = useRouter();
  const load = () => api("/api/lint/status").then(setStatus).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);

  const last = status?.last_run;
  const running = status?.job_running === "lint";
  const flow = ["변경된 위키만 선별", "위키 클레임 즉석 추출", "룰 판정 먼저", "애매한 것만 LLM", "모순·빈틈 → 확인 요청함"];

  return (
    <section className="scr">
      <div className="kicker">관리 · 린트 실행</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">린트 실행</div>
        <button className="btn btn-cta" disabled={running}
          onClick={() => api("/api/lint/run", { method: "POST" }).then(load).catch(() => {})}>
          {running ? "배치 린트 실행 중…" : "배치 린트 지금 실행"}
        </button>
      </div>
      <div className="lede mb24">
        린트는 인제스트와 분리된 배치입니다. 변경된 위키만 선별 → 위키 클레임 즉석 추출(저장 안 함) →
        관련 소스 클레임과 대조 → 룰로 먼저 걸러내고 애매한 쌍만 LLM(Sonnet 4.6)이 판정합니다.
      </div>

      <div className="grid3 mb24">
        <div className="card-lg pad24">
          <div className="fs13 stone mb6">마지막 배치</div>
          <div className="fs24 disp w5 mb4">{last ? fmtDate(last.started_at).split(" ").slice(-2).join(" ") : "—"}</div>
          <div className="fs13 chc">{last ? `변경 위키 ${last.pages_checked}개 검사` : "실행 이력 없음"}</div>
        </div>
        <div className="card-lg pad24">
          <div className="fs13 stone mb6">LLM 호출</div>
          <div className="fs24 disp w5 mb4">{last?.llm_calls ?? 0}회</div>
          <div className="fs13 chc">룰 통과 후 애매한 쌍만</div>
        </div>
        <div className="card-lg pad24">
          <div className="fs13 stone mb6">생성 이슈</div>
          <div className="fs24 disp w5 mb4">{(last?.contradictions ?? 0) + (last?.gaps ?? 0)}건</div>
          <div className="fs13 chc">모순 {last?.contradictions ?? 0} · 빈틈 {last?.gaps ?? 0}</div>
        </div>
      </div>

      <div className="fx ac gap4 mb16" style={{ flexWrap: "wrap" }}>
        {flow.map((f, i) => (
          <span key={f} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span className={`fchip ${i === flow.length - 1 ? "cur" : "done"}`}>{f}</span>
            {i < flow.length - 1 && <span className="farrow">›</span>}
          </span>
        ))}
      </div>

      <div className="card pad24" style={{ background: "var(--soft)", border: "none", borderRadius: 24 }}>
        <div className="fx ac gap12">
          <div className="fs14 chc" style={{ flex: 1 }}>
            생성된 모순·빈틈은 <b>확인 요청함</b>에 역질문으로 게시되며, 답변 시 소스 클레임에 human_confirmed가
            기록되고 위키에 즉시 반영됩니다.
          </div>
          <button className="btn btn-ghost" onClick={() => router.push("/inbox")}>확인 요청함 열기</button>
        </div>
      </div>
    </section>
  );
}

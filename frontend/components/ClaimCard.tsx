"use client";
import { SOURCE_LABEL } from "@/lib/api";

/** 받침 유무에 따라 조사를 고른다 (한글로 끝나지 않으면 받침형 기본). */
function josa(word: string, withBatchim: string, without: string): string {
  const ch = (word || "").trim().slice(-1);
  const code = ch.charCodeAt(0) - 0xac00;
  if (code < 0 || code > 11171) return withBatchim; // 숫자·영문으로 끝나면 "은/이다" 계열
  return code % 28 > 0 ? withBatchim : without;
}

/** 클레임 요소를 명제 문장으로 조합한다.
 * 프로젝트 맥락까지 붙여 "두레로지스 배송 API의 담당자는 [이준호]이다"처럼 자립적으로 읽히게 한다.
 * (주어에 프로젝트명 토큰이 이미 들어있으면 중복해서 붙이지 않는다) */
function Proposition({ claim, compact }: { claim: any; compact?: boolean }) {
  const pred = claim.predicate ?? "";
  const obj = String(claim.object ?? "");
  let subj = claim.subject ?? "";
  const proj = (claim.project ?? "").trim();
  if (proj) {
    const overlap = proj.split(/\s+/).some((t: string) => t && subj.includes(t));
    if (!overlap) subj = `${proj} ${subj}`;
  }
  const tail = claim.polarity === "negative"
    ? ` ${josa(obj, "이", "가")} 아니다`
    : josa(obj, "이다", "다");
  return (
    <div className={compact ? "fs14" : "fs16"} style={{ lineHeight: 1.55, fontWeight: 500 }}>
      {pred ? <>{subj}의 {pred}{josa(pred, "은", "는")} </> : <>{subj}{josa(subj, "은", "는")} </>}
      <b style={{ color: claim.polarity === "negative" ? "var(--crit)" : "var(--primary-deep)" }}>{obj}</b>
      {tail}
    </div>
  );
}

/** 클레임 카드 — 본문은 요소를 조합한 명제 문장, 조건·메타는 칩으로. */
export default function ClaimCard({
  claim, label, tone = "blue", compact,
}: {
  claim: any;
  label?: string;               // "클레임 A" 등 상단 라벨
  tone?: "blue" | "red" | "plain";
  compact?: boolean;            // 사이드 패널용 축약 (패딩·글자 축소)
}) {
  const bg = tone === "blue" ? "rgba(0,145,255,.07)" : tone === "red" ? "#fdeef0" : "var(--soft)";
  const bar = tone === "blue" ? "#0064E0" : tone === "red" ? "#d64550" : "#c6ccd4";
  const srcLabel = claim.source
    ? `${SOURCE_LABEL[claim.source.type] ?? claim.source.type} · ${claim.source.title ?? claim.source.path}`
    : null;

  return (
    <div style={{ borderRadius: compact ? 12 : 16, padding: compact ? "12px 14px" : "16px 18px",
      background: bg, borderLeft: `3px solid ${bar}`, opacity: claim.superseded ? 0.55 : 1 }}>
      <div className="fx jb ac mb8">
        <span className="fs12 w7 sub">{label ?? `클레임 #${claim.id}`}</span>
        <span className="fx gap4">
          {claim.superseded && <span className="bdg bdg-soft">대체됨</span>}
          {claim.human_confirmed && <span className="bdg bdg-success">확인됨</span>}
        </span>
      </div>

      <Proposition claim={claim} compact={compact} />

      <div className="fx gap6 mt10" style={{ flexWrap: "wrap" }}>
        {claim.scope && <span className="bdg bdg-soft">범위 · {claim.scope}</span>}
        {claim.time_scope && <span className="bdg bdg-soft">시점 · {claim.time_scope}</span>}
        {claim.owner && <span className="bdg bdg-soft">담당 {claim.owner}</span>}
        {claim.confidence != null && <span className="bdg bdg-soft">확신도 {Math.round(claim.confidence * 100)}%</span>}
      </div>

      {srcLabel && (
        <div className="fs12 stone mt8" style={{ opacity: 0.85 }}>출처: {srcLabel}</div>
      )}
    </div>
  );
}

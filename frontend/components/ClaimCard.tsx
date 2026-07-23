"use client";
import { SOURCE_LABEL } from "@/lib/api";

/** 클레임을 구조적으로 보여주는 카드.
 * rendered_text를 통짜로 붙이지 않고 주어·속성 / 값 / 담당·프로젝트·시점 필드를 분리해 표시한다. */
export default function ClaimCard({
  claim, label, tone = "blue", highlight,
}: {
  claim: any;
  label?: string;               // "클레임 A" 등 상단 라벨
  tone?: "blue" | "red" | "plain";
  highlight?: string;           // 이 값과 일치하는 선택지를 강조하고 싶을 때 (미사용 시 무시)
}) {
  const bg = tone === "blue" ? "rgba(0,145,255,.07)" : tone === "red" ? "#fdeef0" : "var(--paper, #fff)";
  const bar = tone === "blue" ? "#0064E0" : tone === "red" ? "#d64550" : "#c6ccd4";
  const srcLabel = claim.source
    ? `${SOURCE_LABEL[claim.source.type] ?? claim.source.type} · ${claim.source.title ?? claim.source.path}`
    : null;
  const negative = claim.polarity === "negative";

  return (
    <div style={{ borderRadius: 16, padding: "16px 18px", background: bg, borderLeft: `3px solid ${bar}` }}>
      <div className="fx jb ac mb8">
        <span className="fs12 w7 sub">{label ?? `클레임 #${claim.id}`}</span>
        {claim.human_confirmed && <span className="bdg bdg-success">확인됨</span>}
      </div>

      <div className="fs13 stone mb2">{claim.subject}{claim.predicate ? ` · ${claim.predicate}` : ""}</div>
      <div className="fs17 w7 mb10" style={{ lineHeight: 1.35 }}>
        {negative && <span style={{ color: "var(--crit)", marginRight: 6 }}>아니다:</span>}
        {claim.object}
      </div>

      <div className="fx gap6" style={{ flexWrap: "wrap" }}>
        {claim.owner && <span className="bdg bdg-soft">담당 {claim.owner}</span>}
        {claim.project && <span className="bdg bdg-soft">{claim.project}</span>}
        {claim.time_scope && <span className="bdg bdg-soft">{claim.time_scope}</span>}
        {claim.scope && <span className="bdg bdg-soft">{claim.scope}</span>}
        {claim.confidence != null && <span className="bdg bdg-soft">확신도 {Math.round(claim.confidence * 100)}%</span>}
      </div>

      {srcLabel && (
        <div className="fs12 stone mt8" style={{ opacity: 0.85 }}>출처: {srcLabel}</div>
      )}
    </div>
  );
}

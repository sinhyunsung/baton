"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import ClaimCard from "@/components/ClaimCard";

const STATUS = [
  { key: "", label: "전체" },
  { key: "confirmed", label: "확인됨" },
  { key: "unconfirmed", label: "미확인" },
  { key: "superseded", label: "대체됨" },
];
const PAGE = 30;

export default function ClaimsPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const [owner, setOwner] = useState("");
  const [project, setProject] = useState("");
  const [status, setStatus] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [owners, setOwners] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const load = (offset = 0) => {
    setLoading(true);
    const p = new URLSearchParams({ q, owner, project, status, limit: String(PAGE), offset: String(offset) });
    api(`/api/claims?${p}`).then((r) => {
      setItems(offset ? (prev) => [...prev, ...r.items] as any : r.items);
      setTotal(r.total); setOwners(r.owners); setProjects(r.projects);
    }).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(() => { load(0); }, [q, owner, project, status]);

  return (
    <section className="scr">
      <div className="kicker">위키 · 클레임</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">클레임</div>
        <span className="bdg bdg-soft">전체 {total}건</span>
      </div>
      <div className="lede mb24">
        문서에서 추출된 지식의 최소 단위입니다. 위키 본문과 챗봇 답변은 전부 이 클레임을 근거로 만들어집니다.
      </div>

      <div className="card pad16 mb20">
        <div className="fx gap8 ac" style={{ flexWrap: "wrap" }}>
          <input className="inp" style={{ height: 40, maxWidth: 320 }} placeholder="내용·담당자·프로젝트 검색"
            value={qInput} onChange={(e) => setQInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setQ(qInput)} />
          <button className="btn btn-ghost" onClick={() => setQ(qInput)}>검색</button>
          <div style={{ flex: 1 }} />
          {STATUS.map((s) => (
            <button key={s.key} className={`pill ${status === s.key ? "on" : ""}`}
              onClick={() => setStatus(s.key)}>{s.label}</button>
          ))}
        </div>
        {(owners.length > 0 || projects.length > 0) && (
          <div className="fx gap6 mt12" style={{ flexWrap: "wrap", alignItems: "center" }}>
            {projects.slice(0, 10).map((p) => (
              <button key={p.name} className={`pill ${project === p.name ? "on" : ""}`}
                onClick={() => setProject(project === p.name ? "" : p.name)}>{p.name} {p.count}</button>
            ))}
            <span style={{ width: 1, height: 18, background: "var(--hairsoft)" }} />
            {owners.slice(0, 10).map((o) => (
              <button key={o.name} className={`pill ${owner === o.name ? "on" : ""}`}
                onClick={() => setOwner(owner === o.name ? "" : o.name)}>{o.name} {o.count}</button>
            ))}
          </div>
        )}
      </div>

      {items.length === 0 && !loading ? (
        <div className="card-lg pad24 chc">조건에 맞는 클레임이 없습니다.</div>
      ) : (
        <div className="two" style={{ gap: 14, alignItems: "start" }}>
          {items.map((c) => (
            <div key={c.id} style={{ cursor: "pointer" }} title="관계 그래프 보기"
              onClick={() => router.push(`/graph?focus=claim:${c.id}`)}>
              <ClaimCard claim={c} tone="plain" />
            </div>
          ))}
        </div>
      )}

      {items.length < total && (
        <div className="fx" style={{ justifyContent: "center", marginTop: 20 }}>
          <button className="btn btn-ghost" disabled={loading} onClick={() => load(items.length)}>
            {loading ? "불러오는 중…" : `더 보기 (${items.length}/${total})`}
          </button>
        </div>
      )}
    </section>
  );
}

"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import GraphView, { GEdge, GNode } from "@/components/GraphView";

function GraphInner() {
  const router = useRouter();
  const params = useSearchParams();
  const focus = params.get("focus") ?? "";
  const [data, setData] = useState<{ nodes: GNode[]; edges: GEdge[] } | null>(null);
  const [nearOnly, setNearOnly] = useState(true);

  useEffect(() => {
    api(`/api/wiki/graph${focus ? `?focus=${encodeURIComponent(focus)}` : ""}`).then(setData).catch(() => {});
  }, [focus]);

  const focusId = focus ? (focus.startsWith("page:") ? `p${focus.split(":")[1]}` : `c${focus.split(":")[1]}`) : undefined;

  const view = useMemo(() => {
    if (!data) return null;
    if (!focus || !nearOnly || !focusId) return data;
    // 초점 노드에서 2단계 이내만 (옵시디언 로컬 그래프)
    const adj = new Map<string, string[]>();
    data.edges.forEach((e) => {
      adj.set(e.a, [...(adj.get(e.a) ?? []), e.b]);
      adj.set(e.b, [...(adj.get(e.b) ?? []), e.a]);
    });
    const keep = new Set([focusId]);
    let frontier = [focusId];
    for (let d = 0; d < 2; d++) {
      const next: string[] = [];
      for (const id of frontier) for (const nb of adj.get(id) ?? [])
        if (!keep.has(nb)) { keep.add(nb); next.push(nb); }
      frontier = next;
    }
    return {
      nodes: data.nodes.filter((n) => keep.has(n.id)),
      edges: data.edges.filter((e) => keep.has(e.a) && keep.has(e.b)),
    };
  }, [data, focus, nearOnly, focusId]);

  const focusNode = data?.nodes.find((n) => n.id === focusId);

  return (
    <section className="scr">
      <div className="kicker">위키 · 그래프</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">관계 그래프</div>
        <div className="fx gap6 ac">
          {focus && (
            <>
              <span className="bdg bdg-ink">초점: {focusNode?.label ?? focus}</span>
              <button className={`pill ${nearOnly ? "on" : ""}`} onClick={() => setNearOnly(!nearOnly)}>주변만</button>
              <button className="pill" onClick={() => router.push("/graph")}>전체 보기</button>
            </>
          )}
          <span className="bdg bdg-soft">페이지 {view?.nodes.filter((n) => n.kind === "page").length ?? 0} · 연결 {view?.edges.length ?? 0}</span>
        </div>
      </div>
      <div className="lede mb16">
        <span style={{ color: "#0064E0" }}>●</span> 위키 페이지 ·
        <span style={{ color: "#7cc0f2" }}>●</span> 클레임(초점일 때) ·
        <span style={{ color: "#0064E0", fontWeight: 700 }}> ―</span> 내부 링크 ·
        <span className="stone"> ┄ </span> 같은 원본 문서.
        노드 클릭 = 페이지 열기, 드래그 = 이동, 휠 = 줌, 빈 곳 드래그 = 화면 이동.
      </div>
      <div className="card-lg" style={{ overflow: "hidden", padding: 4 }}>
        {view ? (
          <GraphView nodes={view.nodes} edges={view.edges} focusId={focusId}
            onNodeClick={(n) => {
              if (n.kind === "page") router.push(`/wiki/${n.ref}`);
              else router.push(`/graph?focus=claim:${n.ref}`);
            }} />
        ) : (
          <div className="pad24 chc">그래프 불러오는 중…</div>
        )}
      </div>
    </section>
  );
}

export default function GraphPage() {
  return <Suspense fallback={<section className="scr"><div className="lede">그래프 불러오는 중…</div></section>}><GraphInner /></Suspense>;
}

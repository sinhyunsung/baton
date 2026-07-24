"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

type Page = { id: number; title: string; folder_path: string; updated_at: string;
  page_type: string; claims: number; confirmed: number; lint_open: number };
const TYPE_LABEL: Record<string, string> = { concept: "개념", entity: "엔티티", topic: "주제" };

type Node = { path: string; name: string; depth: number; total: number; children: Node[] };

/** folder_path 목록으로 계층 트리 구성 — 중간 폴더도 노드로 만들고, 카운트는 하위 포함 집계 */
function buildTree(pages: Page[]): Node[] {
  const root: Node = { path: "", name: "", depth: 0, total: 0, children: [] };
  const byPath = new Map<string, Node>([["", root]]);
  for (const p of pages) {
    const segs = p.folder_path.split("/").filter(Boolean);
    let cur = "";
    for (let d = 0; d < segs.length; d++) {
      const parentPath = cur;
      cur = cur ? `${cur}/${segs[d]}` : segs[d];
      if (!byPath.has(cur)) {
        const node: Node = { path: cur, name: segs[d], depth: d + 1, total: 0, children: [] };
        byPath.set(cur, node);
        byPath.get(parentPath)!.children.push(node);
      }
    }
    // 이 페이지가 속한 폴더와 모든 조상의 카운트 증가 (하위 포함 집계)
    let acc = "";
    for (const seg of segs) {
      acc = acc ? `${acc}/${seg}` : seg;
      byPath.get(acc)!.total += 1;
    }
  }
  // 정렬: 글자 수 우선, 같은 길이면 ㄱㄴㄷ (예: 가가가 → 나나나 → 다다다 → 가가가가가)
  const byLenKo = (a: string, b: string) => a.length - b.length || a.localeCompare(b, "ko");
  const sortRec = (n: Node) => { n.children.sort((a, b) => byLenKo(a.name, b.name)); n.children.forEach(sortRec); };
  sortRec(root);
  return root.children;
}

function ago(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime()) / 86400000);
  return d <= 0 ? "오늘" : d === 1 ? "어제" : `${d}일 전`;
}

export default function WikiTree() {
  const [pages, setPages] = useState<Page[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  useEffect(() => { api("/api/wiki/tree").then((r) => setPages(r.pages)).catch(() => {}); }, []);

  const tree = useMemo(() => buildTree(pages), [pages]);
  // 선택 폴더의 페이지 = 그 폴더 + 모든 하위 폴더의 페이지
  const shown = (sel
    ? pages.filter((p) => p.folder_path === sel || p.folder_path.startsWith(sel + "/"))
    : pages
  ).slice().sort((a, b) => a.title.length - b.title.length || a.title.localeCompare(b.title, "ko"));

  function toggle(path: string) {
    setCollapsed((c) => {
      const n = new Set(c);
      n.has(path) ? n.delete(path) : n.add(path);
      return n;
    });
  }

  function renderNode(n: Node): React.ReactNode {
    const isCollapsed = collapsed.has(n.path);
    return (
      <div key={n.path}>
        <div className={`treerow ${sel === n.path ? "on" : ""}`}
          style={{ paddingLeft: 12 + (n.depth - 1) * 16 }}
          onClick={() => setSel(sel === n.path ? null : n.path)}>
          <span onClick={(e) => { e.stopPropagation(); if (n.children.length) toggle(n.path); }}
            style={{ width: 14, cursor: n.children.length ? "pointer" : "default", opacity: n.children.length ? 1 : 0.25 }}>
            {n.children.length ? (isCollapsed ? "▸" : "▾") : "·"}
          </span>
          <span style={{ flex: 1, fontWeight: n.depth === 1 ? 700 : 500 }}>{n.name}</span>
          <span className={`fs12 ${sel === n.path ? "" : "stone"}`}>{n.total}</span>
        </div>
        {!isCollapsed && n.children.map(renderNode)}
      </div>
    );
  }

  return (
    <section className="scr">
      <div className="kicker">위키 · 트리</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">위키 트리</div>
      </div>
      <div className="lede mb24">
        부서·프로젝트·공통 계층으로 저장됩니다. 폴더를 클릭하면 하위 폴더의 페이지까지 함께 표시되고,
        ▾를 누르면 접을 수 있어요. 위키는 시스템이 자동으로 갱신합니다.
      </div>
      {pages.length === 0 ? (
        <div className="card-lg pad24 chc">아직 위키 페이지가 없습니다. 데이터 소스에서 인제스트를 실행해보세요.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 24 }}>
          <div className="card-lg pad20" style={{ alignSelf: "flex-start" }}>
            <div className="fs12 w7 sub mb10" style={{ paddingLeft: 12 }}>전체 위키</div>
            <div className={`treerow ${sel === null ? "on" : ""}`} onClick={() => setSel(null)}>
              <span style={{ width: 14 }}>▾</span>
              <span style={{ flex: 1, fontWeight: 700 }}>전체</span>
              <span className={`fs12 ${sel === null ? "" : "stone"}`}>{pages.length}</span>
            </div>
            {tree.map(renderNode)}
          </div>
          <div className="fx col gap12">
            {sel && (
              <div className="fs13 stone">📁 {sel.replaceAll("/", " › ")} — {shown.length}개 페이지 (하위 폴더 포함)</div>
            )}
            {shown.map((p) => (
              <Link key={p.id} href={`/wiki/${p.id}`}>
                <div className="card pad24">
                  <div className="fx jb ac mb6">
                    <div className="fx ac gap8">
                      <div className="fs18 w7">{p.title}</div>
                      <span className="bdg bdg-soft">{TYPE_LABEL[p.page_type] ?? "개념"}</span>
                      {p.lint_open > 0 && <span className="bdg bdg-attn">린트 {p.lint_open}건</span>}
                    </div>
                    {p.confirmed > 0
                      ? <span className="bdg bdg-success">확인됨 {p.confirmed}</span>
                      : <span className="bdg bdg-blue">소스 클레임</span>}
                  </div>
                  <div className="fs12 stone mb10">{p.folder_path.replaceAll("/", " › ")}</div>
                  <div className="fx jb ac">
                    <span className="fs12 stone">소스 클레임 {p.claims} · 확인됨 {p.confirmed}</span>
                    <span className="fs12 stone">{ago(p.updated_at)} 갱신</span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

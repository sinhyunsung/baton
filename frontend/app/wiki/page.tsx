"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

type Page = { id: number; title: string; folder_path: string; updated_at: string;
  page_type: string; claims: number; confirmed: number; lint_open: number };
const TYPE_LABEL: Record<string, string> = { concept: "개념", entity: "엔티티", topic: "주제" };

function ago(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime()) / 86400000);
  return d <= 0 ? "오늘" : d === 1 ? "어제" : `${d}일 전`;
}

export default function WikiTree() {
  const [pages, setPages] = useState<Page[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { api("/api/wiki/tree").then((r) => setPages(r.pages)).catch(() => {}); }, []);

  const folders = useMemo(() => {
    const f: Record<string, Page[]> = {};
    for (const p of pages) (f[p.folder_path] ||= []).push(p);
    return f;
  }, [pages]);
  const names = Object.keys(folders).sort();
  const shown = sel ? folders[sel] ?? [] : pages;

  return (
    <section className="scr">
      <div className="kicker">위키 · 트리</div>
      <div className="fx jb mb12" style={{ alignItems: "flex-end" }}>
        <div className="h1">위키 트리</div>
      </div>
      <div className="lede mb24">
        조직 › 부서 › 프로젝트 › 개인 계층으로 저장됩니다. 프로젝트는 여러 부서에 속할 수 있고(N:M),
        전사 공통 지식은 공통 폴더에 한 번만 저장돼요. 위키는 시스템만 갱신하며, 반영 전 갱신안 승인을 거칩니다.
      </div>
      {pages.length === 0 ? (
        <div className="card-lg pad24 chc">아직 위키 페이지가 없습니다. 데이터 소스에서 인제스트를 실행하고 <Link href="/proposals">갱신안</Link>을 승인해보세요.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 24 }}>
          <div className="card-lg pad20" style={{ alignSelf: "flex-start" }}>
            <div className="fs12 w7 sub mb10" style={{ paddingLeft: 12 }}>두레테크</div>
            <div className={`treerow ${sel === null ? "on" : ""}`} onClick={() => setSel(null)}>
              <span>▾</span><span style={{ flex: 1, fontWeight: 700 }}>전체</span>
              <span className={`fs12 ${sel === null ? "" : "stone"}`}>{pages.length}</span>
            </div>
            {names.map((f) => (
              <div key={f} className={`treerow ${sel === f ? "on" : ""}`} onClick={() => setSel(f)}
                style={{ paddingLeft: 12 + (f.split("/").length - 1) * 14 }}>
                <span>{sel === f ? "▾" : "▸"}</span>
                <span style={{ flex: 1 }}>{f.split("/").pop()}</span>
                <span className={`fs12 ${sel === f ? "" : "stone"}`}>{folders[f].length}</span>
              </div>
            ))}
          </div>
          <div className="fx col gap12">
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
                  <div className="fs12 stone mb10">{p.folder_path}</div>
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

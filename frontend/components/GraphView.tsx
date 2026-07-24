"use client";
import { useEffect, useRef } from "react";

export type GNode = { id: string; kind: "page" | "claim"; ref: number; label: string; group: string; size: number; confirmed?: boolean };
export type GEdge = { a: string; b: string; kind: "link" | "source" | "claim" };

const PAGE_COLOR = "#0064E0";   // 위키 페이지 — 진한 파랑
const CLAIM_COLOR = "#7cc0f2";  // 클레임 — 하늘색

type Sim = GNode & { x: number; y: number; vx: number; vy: number; fixed?: boolean };

/** 옵시디언식 포스 다이렉트 그래프 (canvas, 외부 라이브러리 없음).
 * 물리: 노드 간 반발 + 엣지 스프링 + 중심 중력. 드래그·휠 줌·팬·클릭 내비게이션 지원. */
export default function GraphView({
  nodes, edges, focusId, onNodeClick, height = 620,
}: {
  nodes: GNode[]; edges: GEdge[]; focusId?: string;
  onNodeClick?: (n: GNode) => void; height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = height;
    canvas.width = W * dpr; canvas.height = H * dpr;

    const sims: Sim[] = nodes.map((n, i) => {
      const ang = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      const r = n.id === focusId ? 0 : 120 + (i % 7) * 40;
      return { ...n, x: W / 2 + Math.cos(ang) * r, y: H / 2 + Math.sin(ang) * r, vx: 0, vy: 0 };
    });
    const byId = new Map(sims.map((s) => [s.id, s]));
    const springs = edges
      .map((e) => ({ a: byId.get(e.a), b: byId.get(e.b), kind: e.kind }))
      .filter((e) => e.a && e.b) as { a: Sim; b: Sim; kind: string }[];
    const degree = new Map<string, number>();
    springs.forEach((e) => { degree.set(e.a.id, (degree.get(e.a.id) ?? 0) + 1); degree.set(e.b.id, (degree.get(e.b.id) ?? 0) + 1); });

    let scale = 1, ox = 0, oy = 0;           // 뷰 변환 (줌·팬)
    let dragging: Sim | null = null, panning = false;
    let px = 0, py = 0, hovered: Sim | null = null;
    let raf = 0, alive = true;

    const toWorld = (cx: number, cy: number) => ({ x: (cx - ox) / scale, y: (cy - oy) / scale });

    function tick() {
      // 반발력 (O(n²) — 수백 노드까지 충분)
      for (let i = 0; i < sims.length; i++) for (let j = i + 1; j < sims.length; j++) {
        const a = sims[i], b = sims[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const f = Math.min(2600 / d2, 8);
        const d = Math.sqrt(d2);
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      }
      // 스프링
      for (const e of springs) {
        const rest = e.kind === "claim" ? 55 : 110;
        const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - rest) * 0.015;
        e.a.vx += (dx / d) * f; e.a.vy += (dy / d) * f;
        e.b.vx -= (dx / d) * f; e.b.vy -= (dy / d) * f;
      }
      // 중심 중력 + 감쇠
      for (const s of sims) {
        s.vx += (W / 2 - s.x) * 0.004; s.vy += (H / 2 - s.y) * 0.004;
        if (s !== dragging) { s.x += (s.vx *= 0.82); s.y += (s.vy *= 0.82); }
      }
    }

    function draw() {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * ox, dpr * oy);

      const neighbor = new Set<string>();
      if (hovered) {
        neighbor.add(hovered.id);
        springs.forEach((e) => { if (e.a === hovered) neighbor.add(e.b.id); if (e.b === hovered) neighbor.add(e.a.id); });
      }
      for (const e of springs) {
        const dim = hovered && !(neighbor.has(e.a.id) && neighbor.has(e.b.id));
        ctx.strokeStyle = e.kind === "link" ? "rgba(0,100,224,.45)" : e.kind === "claim" ? "rgba(150,160,175,.35)" : "rgba(150,160,175,.22)";
        if (dim) ctx.strokeStyle = "rgba(150,160,175,.08)";
        ctx.lineWidth = (e.kind === "link" ? 1.6 : 1) / scale;
        ctx.setLineDash(e.kind === "source" ? [4 / scale, 4 / scale] : []);
        ctx.beginPath(); ctx.moveTo(e.a.x, e.a.y); ctx.lineTo(e.b.x, e.b.y); ctx.stroke();
      }
      ctx.setLineDash([]);
      for (const s of sims) {
        const dim = hovered && !neighbor.has(s.id);
        const color = s.kind === "claim" ? CLAIM_COLOR : PAGE_COLOR;
        ctx.globalAlpha = dim ? 0.15 : 1;
        ctx.fillStyle = color;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2); ctx.fill();
        if (s.id === focusId) {
          ctx.strokeStyle = "#111827"; ctx.lineWidth = 2 / scale;
          ctx.beginPath(); ctx.arc(s.x, s.y, s.size + 3.5 / scale, 0, Math.PI * 2); ctx.stroke();
        }
        const showLabel = scale > 0.65 || s === hovered || s.id === focusId;
        if (showLabel) {
          ctx.font = `${(s === hovered ? 13 : 11.5) / scale}px Pretendard, sans-serif`;
          ctx.fillStyle = dim ? "rgba(70,80,95,.25)" : "#3d4855";
          ctx.textAlign = "center";
          ctx.fillText(s.label, s.x, s.y + s.size + 13 / scale);
        }
        ctx.globalAlpha = 1;
      }
    }

    function loop() {
      if (!alive) return;
      tick(); draw();
      raf = requestAnimationFrame(loop);
    }
    loop();

    const pick = (cx: number, cy: number): Sim | null => {
      const w = toWorld(cx, cy);
      let best: Sim | null = null, bd = 1e9;
      for (const s of sims) {
        const d = Math.hypot(s.x - w.x, s.y - w.y);
        if (d < Math.max(s.size + 6, 10) && d < bd) { best = s; bd = d; }
      }
      return best;
    };
    const pos = (ev: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };

    let downAt = { x: 0, y: 0 }, moved = false;
    const onDown = (ev: PointerEvent) => {
      const { x, y } = pos(ev);
      downAt = { x, y }; moved = false;
      const hit = pick(x, y);
      if (hit) { dragging = hit; } else { panning = true; }
      px = x; py = y;
      canvas.setPointerCapture(ev.pointerId);
    };
    const onMove = (ev: PointerEvent) => {
      const { x, y } = pos(ev);
      if (Math.hypot(x - downAt.x, y - downAt.y) > 4) moved = true;
      if (dragging) {
        const w = toWorld(x, y);
        dragging.x = w.x; dragging.y = w.y; dragging.vx = 0; dragging.vy = 0;
      } else if (panning) {
        ox += x - px; oy += y - py;
      } else {
        hovered = pick(x, y);
        canvas.style.cursor = hovered ? "pointer" : "grab";
      }
      px = x; py = y;
    };
    const onUp = (ev: PointerEvent) => {
      const { x, y } = pos(ev);
      if (!moved) {
        const hit = pick(x, y);
        if (hit && onNodeClick) onNodeClick(hit);
      }
      dragging = null; panning = false;
    };
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const { x, y } = { x: ev.offsetX, y: ev.offsetY };
      const k = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      const ns = Math.min(Math.max(scale * k, 0.25), 3.5);
      ox = x - ((x - ox) / scale) * ns; oy = y - ((y - oy) / scale) * ns;
      scale = ns;
    };
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      alive = false; cancelAnimationFrame(raf);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [nodes, edges, focusId, height, onNodeClick]);

  return <canvas ref={canvasRef} style={{ width: "100%", height, display: "block", borderRadius: 20, background: "var(--canvas)" }} />;
}

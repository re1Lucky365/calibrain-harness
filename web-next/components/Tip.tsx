"use client";
import { useEffect, useRef } from "react";

/** One shared tooltip for every element with data-tip (plain text; lines split on " · " stay inline). */
export function Tip() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current!;
    const move = (e: MouseEvent) => {
      const t = (e.target as HTMLElement | null)?.closest?.("[data-tip]") as HTMLElement | null;
      if (!t) {
        el.hidden = true;
        return;
      }
      el.hidden = false;
      el.textContent = "";
      for (const [i, line] of (t.dataset.tip || "").split("\n").entries()) {
        if (i) el.appendChild(document.createElement("br"));
        el.appendChild(document.createTextNode(line));
      }
      const w = el.offsetWidth, h = el.offsetHeight;
      el.style.left = Math.min(e.clientX + 14, innerWidth - w - 6) + "px";
      el.style.top = Math.min(e.clientY + 14, innerHeight - h - 30) + "px";
    };
    document.addEventListener("mousemove", move);
    return () => document.removeEventListener("mousemove", move);
  }, []);
  return <div ref={ref} className="tip" hidden />;
}

import { useEffect, useRef } from "react";

// Fondo de interferencia Cyber Y2K (HTML5 Canvas): barras de glitch, bloques
// pixelados que parpadean y distorsión magnética según la posición del cursor.
// Corre en requestAnimationFrame (~60fps). El color de acento se lee del tema y
// se recarga al cambiar claro/oscuro (MutationObserver sobre la clase de <html>).
export function GlitchBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const ctx = canvas.getContext("2d");
    if (ctx === null) return;

    let width = 0;
    let height = 0;
    let rafId = 0;
    const mouse = { x: -9999, y: -9999 };

    const readAccent = (): string => {
      const value = getComputedStyle(document.documentElement)
        .getPropertyValue("--color-mint")
        .trim();
      return value || "#ff0033";
    };
    let accent = readAccent();

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    const onMove = (event: MouseEvent) => {
      mouse.x = event.clientX;
      mouse.y = event.clientY;
    };
    window.addEventListener("mousemove", onMove);

    const observer = new MutationObserver(() => {
      accent = readAccent();
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    const RADIUS = 180;

    const draw = () => {
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = accent;

      // Barras horizontales de interferencia.
      const bars = 36;
      for (let i = 0; i < bars; i++) {
        const y = Math.random() * height;
        const h = Math.random() * 4 + 1;
        let x = Math.random() * width;
        const w = Math.random() * 90 + 12;

        const dx = mouse.x - x;
        const dy = mouse.y - y;
        const dist = Math.hypot(dx, dy);
        let alpha = Math.random() * 0.14 + 0.02;
        if (dist < RADIUS) {
          const force = (1 - dist / RADIUS) * 28;
          x += dx * force * 0.02;
          alpha = Math.min(0.4, alpha + force * 0.01);
        }

        ctx.globalAlpha = alpha;
        ctx.fillRect(x, y, w, h);
      }

      // Bloques pixelados que parpadean (estética virus/vaporwave).
      const pixels = 24;
      for (let i = 0; i < pixels; i++) {
        const s = Math.random() < 0.5 ? 2 : 3;
        const x = Math.floor((Math.random() * width) / s) * s;
        const y = Math.floor((Math.random() * height) / s) * s;
        const dx = mouse.x - x;
        const dy = mouse.y - y;
        const dist = Math.hypot(dx, dy);
        ctx.globalAlpha = dist < RADIUS ? 0.35 : Math.random() < 0.25 ? 0.25 : 0.04;
        ctx.fillRect(x, y, s, s);
      }

      ctx.globalAlpha = 1;
      rafId = requestAnimationFrame(draw);
    };
    rafId = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onMove);
      observer.disconnect();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="pointer-events-none fixed inset-0 z-0"
      aria-hidden="true"
    />
  );
}

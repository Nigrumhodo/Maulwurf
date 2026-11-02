import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

// Sprite en rejilla (filas × columnas). A diferencia de `SpriteAnimator`/`AvatarSprite`,
// avanza por toda la hoja en orden fila por fila, no solo por una fila. Se usa para la
// hoja «a la espera» (4 columnas × 2 filas) del compañero del dashboard.
export interface GridSpriteProps {
  src: string;
  /** tamaño completo de la hoja en px */
  sheetWidth: number;
  sheetHeight: number;
  columns: number;
  rows: number;
  /** alto de visualización de un fotograma en px */
  height: number;
  fps?: number;
  /** reproducir en bucle (false se detiene en el último fotograma) */
  loop?: boolean;
  className?: string;
}

export function GridSprite({
  src,
  sheetWidth,
  sheetHeight,
  columns,
  rows,
  height,
  fps = 6,
  loop = true,
  className = "",
}: GridSpriteProps) {
  const total = columns * rows;
  const frameWidth = sheetWidth / columns;
  const frameHeight = sheetHeight / rows;
  const scale = height / frameHeight;
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (total <= 1) return;
    // Animación decorativa: con movimiento reducido se queda en el primer fotograma.
    if (
      typeof window.matchMedia !== "function" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    const id = window.setInterval(() => {
      setIndex((current) => {
        if (!loop && current >= total - 1) return current;
        return (current + 1) % total;
      });
    }, 1000 / fps);
    return () => window.clearInterval(id);
  }, [total, fps, loop]);

  const column = index % columns;
  const row = Math.floor(index / columns);
  const style = {
    width: `${frameWidth * scale}px`,
    height: `${height}px`,
    backgroundImage: `url("${src}")`,
    backgroundRepeat: "no-repeat",
    backgroundSize: `${sheetWidth * scale}px ${sheetHeight * scale}px`,
    backgroundPosition: `${-(column * frameWidth * scale)}px ${-(row * frameHeight * scale)}px`,
    imageRendering: "pixelated",
  } as CSSProperties;

  return (
    <div
      className={`inline-block ${className}`}
      style={style}
      role="img"
      aria-label="animación del compañero"
    />
  );
}

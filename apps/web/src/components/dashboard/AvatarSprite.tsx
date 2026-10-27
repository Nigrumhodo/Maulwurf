import type { CSSProperties } from "react";

// D- (Dashboard) — Sprite de una sola fila con altura de visualización fija.
// Como las hojas (clara/oscura) tienen distinta resolución, se calcula un
// `scale` fraccionario a partir de la altura objetivo; el ancho sale de la
// proporción del fotograma. Reutiliza el keyframe `sprite-move` de index.css.
interface AvatarSpriteProps {
  src: string;
  /** ancho total de la hoja (px) */
  sheetWidth: number;
  /** alto total de la hoja (px, una sola fila) */
  sheetHeight: number;
  /** número de fotogramas en la fila */
  frames: number;
  /** altura de visualización (px) */
  height: number;
  fps?: number;
  className?: string;
}

export function AvatarSprite({
  src,
  sheetWidth,
  sheetHeight,
  frames,
  height,
  fps = 12,
  className = "",
}: AvatarSpriteProps) {
  const frameWidth = sheetWidth / frames;
  const scale = height / sheetHeight;
  const width = frameWidth * scale;
  const endX = -(frameWidth * (frames - 1)) * scale;
  const duration = frames / fps;

  const style = {
    width: `${width}px`,
    height: `${height}px`,
    backgroundImage: `url("${src}")`,
    backgroundRepeat: "no-repeat",
    backgroundSize: `${sheetWidth * scale}px ${height}px`,
    animation: `sprite-move ${duration}s steps(${frames - 1}) infinite`,
    "--sprite-y": "0px",
    "--sprite-end": `${endX}px`,
  } as CSSProperties & Record<string, string>;

  return (
    <div className={`inline-block ${className}`} style={style} role="img" aria-label="avatar animado" />
  );
}

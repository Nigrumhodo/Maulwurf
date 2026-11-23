export interface RetroVisualProps {
  /** Animated WebP (or primary pixel-art image) rendered when motion is allowed. */
  src: string;
  /** Static WebP frame served automatically when `prefers-reduced-motion: reduce` is active. */
  staticSrc?: string;
  alt?: string;
  className?: string;
}

/**
 * Renders a crisp pixelated retro asset and honours `prefers-reduced-motion: reduce`
 * via native `<picture>` source selection so animated WebP illustrations stay calm
 * without JavaScript timers.
 */
export function RetroVisual({
  src,
  staticSrc,
  alt = "",
  className = "",
}: RetroVisualProps) {
  const img = (
    <img
      src={src}
      alt={alt}
      aria-hidden={alt === "" ? true : undefined}
      className={className}
      style={{ imageRendering: "pixelated" }}
    />
  );

  if (staticSrc === undefined) return img;

  return (
    <picture className="contents">
      <source media="(prefers-reduced-motion: reduce)" srcSet={staticSrc} />
      {img}
    </picture>
  );
}

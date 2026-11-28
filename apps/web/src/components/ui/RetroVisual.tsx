export interface RetroVisualProps {
  /** Animated WebP (or primary pixel-art image) rendered in dark mode, or both themes if `lightSrc` is omitted. */
  src: string;
  /** Static WebP frame served automatically when `prefers-reduced-motion: reduce` is active. */
  staticSrc?: string;
  /** Light-theme counterpart rendered under `:root.light` via `.img-light-only`. */
  lightSrc?: string;
  /** Static reduced-motion frame for `lightSrc`. */
  lightStaticSrc?: string;
  alt?: string;
  className?: string;
}

function renderPicture(
  src: string,
  staticSrc: string | undefined,
  alt: string,
  className: string,
) {
  const img = (
    <img
      src={src}
      alt={alt}
      aria-hidden={alt === "" ? true : undefined}
      className={className}
      style={{ imageRendering: "pixelated" }}
      // Lazy loading keeps the hidden opposite-theme variant from downloading until toggled.
      loading="lazy"
      decoding="async"
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

/**
 * Renders a crisp pixelated retro asset, honours `prefers-reduced-motion: reduce`
 * via native `<picture>` source selection, and optionally switches between dark and
 * light theme artwork via `.img-dark-only` / `.img-light-only` without React state.
 */
export function RetroVisual({
  src,
  staticSrc,
  lightSrc,
  lightStaticSrc,
  alt = "",
  className = "",
}: RetroVisualProps) {
  if (lightSrc === undefined) {
    return renderPicture(src, staticSrc, alt, className);
  }

  return (
    <>
      <span className="img-dark-only">{renderPicture(src, staticSrc, alt, className)}</span>
      <span className="img-light-only">{renderPicture(lightSrc, lightStaticSrc, alt, className)}</span>
    </>
  );
}

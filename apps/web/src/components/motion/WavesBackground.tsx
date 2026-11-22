// Decorative pixel-art waves layer, fixed to the bottom of the viewport and hidden in the light
// theme. Layouts mount it as the first child of their root; it is static and never interactive.
export function WavesBackground() {
  return <div className="waves-bg" aria-hidden="true" />;
}

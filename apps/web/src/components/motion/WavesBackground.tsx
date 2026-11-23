// Decorative pixel-art waves layer, fixed to the bottom of the viewport and shown in both themes
// (CSS picks the dark or light image). Layouts mount it as the first child of their root; it is
// static and never interactive.
export function WavesBackground() {
  return <div className="waves-bg" aria-hidden="true" />;
}

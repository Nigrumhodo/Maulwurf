import type { CSSProperties } from 'react'

export interface SpriteAnimatorProps {
  /** image source (url or data uri) of the sprite sheet */
  src: string
  /** width in px of a single frame */
  frameWidth: number
  /** height in px of a single frame */
  frameHeight: number
  /** total number of frames in a single row */
  frames: number
  /** frames per second */
  fps?: number
  /** loop the animation (false plays once and holds last frame) */
  loop?: boolean
  /** row index for multi-row sheets (0-based) */
  row?: number
  /** integer scale factor (kept pixelated) */
  scale?: number
  className?: string
}

export function SpriteAnimator({
  src,
  frameWidth,
  frameHeight,
  frames,
  fps = 8,
  loop = true,
  row = 0,
  scale = 1,
  className = '',
}: SpriteAnimatorProps) {
  const w = frameWidth * scale
  const h = frameHeight * scale
  const sheetW = frameWidth * frames * scale
  const sheetH = frameHeight * scale
  const endX = -(frameWidth * (frames - 1)) * scale
  const startY = -(row * frameHeight) * scale

  const duration = frames / fps

  const style = {
    width: `${w}px`,
    height: `${h}px`,
    backgroundImage: `url("${src}")`,
    backgroundRepeat: 'no-repeat',
    backgroundSize: `${sheetW}px ${sheetH}px`,
    imageRendering: 'pixelated',
    '--sprite-y': `${startY}px`,
    '--sprite-end': `${endX}px`,
  } as CSSProperties & Record<string, string>

  if (frames > 1) {
    style.animation = `sprite-move ${duration}s steps(${frames - 1}) infinite`
    if (!loop) {
      style.animationIterationCount = '1'
      style.animationFillMode = 'forwards'
    }
  }

  return (
    <div
      className={`inline-block ${className}`}
      style={style}
      role="img"
      aria-label="sprite animation"
    />
  )
}

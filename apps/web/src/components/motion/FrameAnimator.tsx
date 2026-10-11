import { useEffect, useState } from 'react'

export interface FrameAnimatorProps {
  /** ordered list of frame image sources */
  frames: string[]
  /** milliseconds per frame (lower = faster) */
  speed?: number
  /** loop the animation (false plays once and holds last frame) */
  loop?: boolean
  className?: string
}

export function FrameAnimator({
  frames,
  speed = 100,
  loop = true,
  className = '',
}: FrameAnimatorProps) {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    if (frames.length <= 1) return
    const id = window.setInterval(() => {
      setIndex((i) => {
        if (!loop && i >= frames.length - 1) return i
        return (i + 1) % frames.length
      })
    }, speed)
    return () => window.clearInterval(id)
  }, [frames, speed, loop])

  return (
    <img
      src={frames[index] ?? frames[0]}
      alt="frame animation"
      draggable={false}
      className={className}
      style={{ imageRendering: 'pixelated' }}
    />
  )
}

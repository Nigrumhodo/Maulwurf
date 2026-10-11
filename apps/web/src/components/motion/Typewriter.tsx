import { useEffect, useState } from 'react'
import { BlinkingCaret } from './BlinkingCaret'

export interface TypewriterProps {
  /** full string to reveal character-by-character */
  text: string
  /** delay in ms between each character */
  speed?: number
  /** restart from the beginning once finished */
  loop?: boolean
  /** show a blinking caret while typing */
  showCaret?: boolean
  className?: string
}

export function Typewriter({
  text,
  speed = 60,
  loop = false,
  showCaret = true,
  className = '',
}: TypewriterProps) {
  const [count, setCount] = useState(0)
  const [prevText, setPrevText] = useState(text)

  // reset the counter when the target text changes (during render, not in an effect)
  if (prevText !== text) {
    setPrevText(text)
    setCount(0)
  }

  useEffect(() => {
    if (count >= text.length) {
      if (!loop) return
      const restart = setTimeout(() => setCount(0), speed * 4)
      return () => clearTimeout(restart)
    }
    const tick = setTimeout(() => setCount((c) => c + 1), speed)
    return () => clearTimeout(tick)
  }, [count, text, speed, loop])

  return (
    <span className={`lowercase font-pixel ${className}`}>
      {text.slice(0, count)}
      {showCaret && <BlinkingCaret />}
    </span>
  )
}

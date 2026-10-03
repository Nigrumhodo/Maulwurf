import type { HTMLAttributes } from 'react'

export interface FlickerTextProps extends HTMLAttributes<HTMLSpanElement> {
  /** use a step-based CRT flicker instead of a blink */
  mode?: 'flicker' | 'blink'
}

export function FlickerText({
  mode = 'flicker',
  className = '',
  children,
  ...rest
}: FlickerTextProps) {
  const animation = mode === 'blink' ? 'animate-blink' : 'animate-flicker'
  return (
    <span className={`lowercase font-pixel ${animation} ${className}`} {...rest}>
      {children}
    </span>
  )
}

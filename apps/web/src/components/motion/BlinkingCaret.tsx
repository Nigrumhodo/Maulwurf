export interface BlinkingCaretProps {
  /** render a filled block caret instead of an underscore */
  block?: boolean
  className?: string
}

export function BlinkingCaret({ block = false, className = '' }: BlinkingCaretProps) {
  if (block) {
    return (
      <span
        className={`inline-block h-[1em] w-[0.6em] animate-caret bg-mint align-text-bottom ${className}`}
        aria-hidden="true"
      />
    )
  }
  return (
    <span className={`animate-caret text-mint ${className}`} aria-hidden="true">
      _
    </span>
  )
}

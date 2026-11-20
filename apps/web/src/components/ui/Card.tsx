import type { HTMLAttributes, ReactNode } from 'react'

export type CardVariant = 'default' | 'outline' | 'accent'

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** text rendered in the terminal title bar */
  title?: ReactNode
  /** optional content rendered in the terminal footer bar */
  footer?: ReactNode
  /** hide the terminal title bar entirely */
  showHeader?: boolean
  /** optional close callback; makes the title-bar [x] interactive */
  onClose?: () => void
  /** visual variant */
  variant?: CardVariant
  as?: 'article' | 'section'
}

const variants: Record<CardVariant, string> = {
  default: 'border-pure-black bg-surface shadow-block',
  outline: 'border-lavender bg-transparent shadow-block',
  accent: 'border-mint bg-surface shadow-block',
}

const barBorder: Record<CardVariant, string> = {
  default: 'border-pure-black',
  outline: 'border-lavender',
  accent: 'border-mint',
}

/** terminal title-bar styling per variant: distinct bg + readable text */
const headerBar: Record<CardVariant, string> = {
  default: 'bg-mint text-on-accent',
  outline: 'bg-mint text-on-accent',
  accent: 'bg-mint text-on-accent',
}

export function Card({
  title,
  footer,
  showHeader = true,
  onClose,
  variant = 'default',
  as: Tag = 'article',
  className = '',
  children,
  ...rest
}: CardProps) {
  return (
    <Tag
      className={`lowercase font-pixel min-w-0 max-w-full border-2 ${variants[variant]} ${className}`}
      {...rest}
    >
      {showHeader && (
        <header
          className={`flex items-center justify-between gap-2 border-b-2 ${barBorder[variant]} ${headerBar[variant]} px-3 py-2`}
        >
          <span className="capitalize min-w-0 truncate font-bold">› {title}</span>
          {onClose !== undefined ? (
            <button
              type="button"
              aria-label="cerrar ventana"
              onClick={onClose}
              className="shrink-0 cursor-pointer font-bold hover:opacity-75"
            >
              [x]
            </button>
          ) : (
            <span className="shrink-0 select-none" aria-hidden="true">
              [x]
            </span>
          )}
        </header>
      )}
      <div className="min-w-0 p-4">{children}</div>
      {footer != null && (
        <footer
          className={`border-t-2 ${barBorder[variant]} px-3 py-2 text-mint`}
        >
          {footer}
        </footer>
      )}
    </Tag>
  )
}

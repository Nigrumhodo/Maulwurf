import type { HTMLAttributes, ReactNode } from 'react'

export type CardVariant = 'default' | 'outline' | 'accent'

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** text rendered in the terminal title bar */
  title?: ReactNode
  /** optional content rendered in the terminal footer bar */
  footer?: ReactNode
  /** hide the terminal title bar entirely */
  showHeader?: boolean
  /** visual variant */
  variant?: CardVariant
  as?: 'article' | 'section'
}

const variants: Record<CardVariant, string> = {
  default: 'border-pure-black bg-dark shadow-block',
  outline: 'border-lavender bg-transparent shadow-block',
  accent: 'border-mint bg-pure-black shadow-block-black',
}

const barBorder: Record<CardVariant, string> = {
  default: 'border-pure-black',
  outline: 'border-lavender',
  accent: 'border-mint',
}

/** terminal title-bar styling per variant: distinct bg + readable text */
const headerBar: Record<CardVariant, string> = {
  default: 'bg-mint text-on-accent',
  outline: 'bg-lavender text-on-accent',
  accent: 'bg-mint text-on-accent',
}

export function Card({
  title,
  footer,
  showHeader = true,
  variant = 'default',
  as: Tag = 'article',
  className = '',
  children,
  ...rest
}: CardProps) {
  return (
    <Tag
      className={`lowercase font-pixel border-2 ${variants[variant]} ${className}`}
      {...rest}
    >
      {showHeader && (
        <header
          className={`flex items-center justify-between border-b-2 ${barBorder[variant]} ${headerBar[variant]} px-3 py-2`}
        >
          <span className="lowercase font-bold">› {title}</span>
          <span className="select-none" aria-hidden="true">
            [x]
          </span>
        </header>
      )}
      <div className="p-4">{children}</div>
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

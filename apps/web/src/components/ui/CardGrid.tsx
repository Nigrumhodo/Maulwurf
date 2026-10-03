import type { HTMLAttributes } from 'react'

export interface CardGridProps extends HTMLAttributes<HTMLDivElement> {
  /** number of columns at the largest breakpoint */
  columns?: 1 | 2 | 3 | 4
}

const cols: Record<1 | 2 | 3 | 4, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 sm:grid-cols-2',
  3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
}

/** responsive grid for laying out cards */
export function CardGrid({
  columns = 3,
  className = '',
  children,
  ...rest
}: CardGridProps) {
  return (
    <div className={`grid gap-6 ${cols[columns]} ${className}`} {...rest}>
      {children}
    </div>
  )
}

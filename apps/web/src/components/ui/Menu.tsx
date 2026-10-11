import type { ReactNode } from 'react'

export interface MenuItem {
  key: string
  label: ReactNode
  disabled?: boolean
  danger?: boolean
}

export interface MenuProps {
  items: MenuItem[]
  /** highlight the item with this key */
  activeKey?: string
  onSelect?: (key: string) => void
  /** layout direction */
  direction?: 'vertical' | 'horizontal'
  className?: string
}

export function Menu({
  items,
  activeKey,
  onSelect,
  direction = 'vertical',
  className = '',
}: MenuProps) {
  return (
    <ul
      role="menu"
      className={`m-0 list-none p-0 lowercase ${direction === 'horizontal' ? 'flex flex-wrap gap-1' : 'flex flex-col gap-1'} ${className}`}
    >
      {items.map((item) => {
        const active = item.key === activeKey
        return (
          <li key={item.key} role="none">
            <button
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => onSelect?.(item.key)}
              className={`border-2 border-pure-black px-3 py-2 text-left text-lavender transition-none hover:border-mint hover:text-mint disabled:opacity-40 ${
                active
                  ? 'bg-mint text-on-accent'
                  : 'bg-transparent'
              } ${item.danger ? 'hover:bg-pure-black hover:text-pure-white' : ''}`}
            >
              {item.label}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

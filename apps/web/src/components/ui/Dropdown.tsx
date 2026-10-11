import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { buttonClasses } from './buttonStyles'

export interface DropdownItem {
  key?: string
  label: ReactNode
  onSelect?: () => void
  disabled?: boolean
  danger?: boolean
}

export interface DropdownProps {
  /** plain content shown on the trigger button (text/icon, not a <button>) */
  trigger: ReactNode
  items: DropdownItem[]
  align?: 'left' | 'right'
  className?: string
}

export function Dropdown({
  trigger,
  items,
  align = 'left',
  className = '',
}: DropdownProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref} className={`relative inline-block ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={buttonClasses('secondary', 'md')}
      >
        {trigger}
        <span aria-hidden="true" className="text-mint">
          ▾
        </span>
      </button>

      {open && (
        <ul
          role="menu"
          className={`absolute z-20 mt-2 min-w-[12rem] border-2 border-pure-black bg-dark shadow-block ${align === 'right' ? 'right-0' : 'left-0'}`}
        >
          {items.map((item, i) => (
            <li key={item.key ?? i} role="none">
              <button
                type="button"
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  item.onSelect?.()
                  setOpen(false)
                }}
                className={`block w-full px-3 py-2 text-left lowercase text-lavender hover:bg-mint hover:text-on-accent disabled:opacity-40 ${item.danger ? 'hover:bg-pure-black hover:text-pure-white' : ''}`}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

import { forwardRef } from 'react'
import type { InputHTMLAttributes } from 'react'

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** label rendered above the field, in terminal-prompt style */
  label?: string
  /** render the label with a `>` prompt prefix */
  prompt?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, prompt = true, id, className = '', ...rest }, ref) => (
    <label className="lowercase font-pixel flex flex-col gap-1">
      {label != null && (
        <span className="text-mint">
          {prompt ? '> ' : ''}
          {label}
        </span>
      )}
      <input
        ref={ref}
        id={id}
        className={`bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint placeholder:text-lavender/40 focus:border-mint focus:outline-none ${className}`}
        {...rest}
      />
    </label>
  ),
)

Input.displayName = 'Input'

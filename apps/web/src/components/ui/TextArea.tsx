import { forwardRef } from 'react'
import type { TextareaHTMLAttributes } from 'react'

export interface TextAreaProps
  extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** label rendered above the field, in terminal-prompt style */
  label?: string
  /** render the label with a `>` prompt prefix */
  prompt?: boolean
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  ({ label, prompt = true, id, className = '', rows = 4, ...rest }, ref) => (
    <label className="lowercase font-pixel flex flex-col gap-1">
      {label != null && (
        <span className="text-mint">
          {prompt ? '> ' : ''}
          {label}
        </span>
      )}
      <textarea
        ref={ref}
        id={id}
        rows={rows}
        className={`bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint resize-y placeholder:text-lavender/40 focus:border-mint focus:outline-none ${className}`}
        {...rest}
      />
    </label>
  ),
)

TextArea.displayName = 'TextArea'

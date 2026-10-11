import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'
import { buttonClasses } from './buttonStyles'
import type { ButtonVariant, ButtonSize } from './buttonStyles'

export type { ButtonVariant, ButtonSize } from './buttonStyles'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', className = '', children, ...rest }, ref) => (
    <button
      ref={ref}
      className={buttonClasses(variant, size, className)}
      {...rest}
    >
      {children}
    </button>
  ),
)

Button.displayName = 'Button'

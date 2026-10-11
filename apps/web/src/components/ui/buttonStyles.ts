export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

const base =
  'lowercase font-pixel inline-flex items-center justify-center gap-2 border-2 border-pure-black cursor-pointer select-none whitespace-nowrap transition-none active:translate-x-[2px] active:translate-y-[2px] disabled:pointer-events-none disabled:opacity-40'

const variants: Record<ButtonVariant, string> = {
  primary:
    'bg-mint text-on-accent shadow-block hover:bg-lavender active:shadow-block-sm',
  secondary:
    'bg-transparent text-lavender shadow-block hover:border-mint hover:text-mint active:shadow-block-sm',
  ghost: 'bg-transparent text-lavender border-transparent hover:text-mint',
  danger:
    'bg-pure-black text-pure-white shadow-block-black hover:text-mint active:shadow-block-sm-black',
}

const sizes: Record<ButtonSize, string> = {
  sm: 'text-lg px-3 py-1',
  md: 'text-xl px-5 py-2',
  lg: 'text-2xl px-8 py-3',
}

/** shared class builder so other controls can reuse the button look */
export function buttonClasses(
  variant: ButtonVariant = 'primary',
  size: ButtonSize = 'md',
  extra = '',
) {
  return `${base} ${variants[variant]} ${sizes[size]} ${extra}`
}

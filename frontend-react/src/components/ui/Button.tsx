import { forwardRef, ButtonHTMLAttributes } from 'react'

function cx(...classes: (string | undefined | null | false)[]) {
  return classes.filter(Boolean).join(' ')
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline'
type Size = 'xs' | 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  fullWidth?: boolean
}

const variantClasses: Record<Variant, string> = {
  primary:
    'bg-brand-red text-white hover:bg-red-700 focus:ring-brand-red disabled:bg-red-300',
  secondary:
    'bg-brand-navy text-white hover:bg-brand-dark focus:ring-brand-navy disabled:bg-gray-400',
  ghost:
    'bg-transparent text-gray-600 hover:bg-gray-100 focus:ring-gray-300 disabled:text-gray-300',
  danger:
    'bg-red-600 text-white hover:bg-red-700 focus:ring-red-500 disabled:bg-red-300',
  outline:
    'bg-transparent border border-gray-300 text-gray-700 hover:bg-gray-50 focus:ring-gray-300 disabled:text-gray-300',
}

const sizeClasses: Record<Size, string> = {
  xs: 'px-2 py-1 text-xs',
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-3 text-base',
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      loading = false,
      fullWidth = false,
      className,
      disabled,
      children,
      ...props
    },
    ref,
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cx(
          'inline-flex items-center justify-center gap-2 rounded-md font-medium',
          'transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1',
          'disabled:cursor-not-allowed',
          variantClasses[variant],
          sizeClasses[size],
          fullWidth && 'w-full',
          className,
        )}
        {...props}
      >
        {loading && (
          <svg
            className="h-4 w-4 animate-spin"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
        )}
        {children}
      </button>
    )
  },
)

Button.displayName = 'Button'

export default Button

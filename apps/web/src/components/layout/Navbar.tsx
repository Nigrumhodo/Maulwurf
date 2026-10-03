import { NavLink, useLocation, useNavigate } from 'react-router-dom'

const pages = [
  { path: '/', label: '01.home' },
  { path: '/cards', label: '02.cards' },
  { path: '/sprites', label: '03.sprites' },
]

export function Navbar() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const idx = pages.findIndex((p) => p.path === pathname)
  const prev = pages[(idx - 1 + pages.length) % pages.length]
  const next = pages[(idx + 1) % pages.length]

  return (
    <nav className="sticky top-0 z-50 border-b-2 border-pure-black bg-dark">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3">
        <NavLink to="/" className="lowercase text-xl text-mint">
          maulwurf<span className="text-lavender">@lab</span>
        </NavLink>

        <div className="flex items-center gap-1">
          {pages.map((p) => (
            <NavLink
              key={p.path}
              to={p.path}
              className={({ isActive }) =>
                `lowercase px-3 py-1 text-lavender hover:text-mint ${
                  isActive ? 'bg-mint text-on-accent' : ''
                }`
              }
            >
              {p.label}
            </NavLink>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => navigate(prev.path)}
            aria-label={`previous: ${prev.label}`}
            className="border-2 border-pure-black px-2 py-1 text-xl leading-none text-mint hover:bg-mint hover:text-on-accent active:translate-x-[1px] active:translate-y-[1px]"
          >
            ‹
          </button>
          <button
            type="button"
            onClick={() => navigate(next.path)}
            aria-label={`next: ${next.label}`}
            className="border-2 border-pure-black px-2 py-1 text-xl leading-none text-mint hover:bg-mint hover:text-on-accent active:translate-x-[1px] active:translate-y-[1px]"
          >
            ›
          </button>
        </div>
      </div>
    </nav>
  )
}

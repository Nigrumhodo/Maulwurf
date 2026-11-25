import type { Ref } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

export interface HeaderProps {
  sectionLabel: string;
  menuOpen: boolean;
  onToggleMenu: () => void;
  menuButtonRef?: Ref<HTMLButtonElement>;
}

// Mobile-only top bar (< md). Above that width the app menu is a fixed column, so the header
// tabs and utilities of earlier waves live in the menu instead.
export function Header({ sectionLabel, menuOpen, onToggleMenu, menuButtonRef }: HeaderProps) {
  const { t } = useTranslation("common");
  return (
    <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b-2 border-pure-black bg-surface px-3 md:hidden">
      <Link to="/" className="inline-flex min-h-10 shrink-0 items-center lowercase text-xl text-mint">
        maulwurf<span className="text-lavender">@lab</span>
      </Link>
      <p className="capitalize min-w-0 flex-1 truncate text-lavender">
        <span className="text-mint" aria-hidden="true">
          ›{" "}
        </span>
        {sectionLabel}
      </p>
      <button
        ref={menuButtonRef}
        type="button"
        onClick={onToggleMenu}
        aria-label={t("sidebar.menu")}
        aria-expanded={menuOpen}
        aria-controls="app-menu"
        className="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center border-2 border-pure-black text-xl leading-none text-lavender shadow-block-sm hover:text-mint"
      >
        <span aria-hidden="true">☰</span>
      </button>
    </header>
  );
}

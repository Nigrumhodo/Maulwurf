import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useAuth } from "@/components/auth/AuthProvider";
import { LegalLinks } from "@/components/legal/LegalLinks";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { UiLanguageSwitcher } from "@/components/ui/UiLanguageSwitcher";
import {
  iconCalendario,
  iconCarpeta,
  iconCassette,
  iconChatCrt,
  iconMadriguera,
  iconTareas,
} from "@/components/Visual";
import { Clock } from "./Clock";
import { Header } from "./Header";
import {
  HOME_ITEM,
  NAV_GROUPS,
  PANEL_ITEM,
  SETTINGS_ITEM,
  currentSectionLabel,
} from "./gestionNav";
import type { AppNavItem } from "./gestionNav";

const NAV_ICONS: Record<string, string> = {
  [HOME_ITEM.to]: iconMadriguera,
  [PANEL_ITEM.to]: iconCarpeta,
  "/gestion/subir": iconCassette,
  "/gestion/biblioteca": iconCarpeta,
  "/gestion/chat": iconChatCrt,
  "/gestion/materias": iconCarpeta,
  "/gestion/tareas": iconTareas,
  "/gestion/agenda": iconCalendario,
};

const GLYPH = "w-6 shrink-0 text-center";

const toggleClasses =
  "min-h-8 min-w-8 shrink-0 items-center justify-center border-2 border-lavender/40 leading-none text-lavender hover:border-mint hover:text-mint";

function rowClasses(rail: boolean, active = false): string {
  return `capitalize flex min-h-10 w-full items-center gap-3 px-3 text-left lg:text-lg ${
    active ? "bg-mint text-on-accent" : "text-lavender hover:text-mint"
  } ${rail ? "md:max-lg:justify-center md:max-lg:px-0" : ""}`;
}

function ItemIcon({ item }: { item: AppNavItem }) {
  const icon = NAV_ICONS[item.to];
  if (icon === undefined) {
    return (
      <span aria-hidden="true" className={GLYPH}>
        {item.glyph}
      </span>
    );
  }
  return (
    <span aria-hidden="true" className="flex w-6 shrink-0 justify-center">
      <img
        src={icon}
        alt=""
        className="h-6 w-6 object-contain"
        style={{ imageRendering: "pixelated" }}
      />
    </span>
  );
}

function MenuLink({ item, rail, onNavigate }: { item: AppNavItem; rail: boolean; onNavigate: () => void }) {
  const { t } = useTranslation("common");
  const label = t(`nav.items.${item.labelKey}`);
  return (
    <NavLink
      to={item.to}
      end={item.end}
      title={label}
      onClick={onNavigate}
      className={({ isActive }) => rowClasses(rail, isActive)}
    >
      <ItemIcon item={item} />
      <span className={`truncate ${rail ? "md:max-lg:sr-only" : ""}`}>{label}</span>
    </NavLink>
  );
}

export interface AppSidebarProps {
  /** Opens the global command palette (Ctrl+K). */
  onOpenSearch?: () => void;
}

// Wave 3 · one menu for every authenticated route. Desktop (lg) keeps a fixed 240px column;
// tablet (md) shows a 56px glyph rail that expands over the content; mobile shows the Header
// top bar and opens the same menu as a full-height drawer.
export function AppSidebar({ onOpenSearch }: AppSidebarProps) {
  const { t } = useTranslation("common");
  const { logout } = useAuth();
  const { pathname } = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [railExpanded, setRailExpanded] = useState(false);
  const [lastPathname, setLastPathname] = useState(pathname);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const railToggleRef = useRef<HTMLButtonElement>(null);
  const drawerWasOpen = useRef(false);
  const rail = !railExpanded;

  // Any navigation (menu, palette, browser history) folds the overlays back.
  if (lastPathname !== pathname) {
    setLastPathname(pathname);
    setDrawerOpen(false);
    setRailExpanded(false);
  }

  useEffect(() => {
    if (drawerOpen) closeButtonRef.current?.focus();
    else if (drawerWasOpen.current) menuButtonRef.current?.focus();
    drawerWasOpen.current = drawerOpen;
  }, [drawerOpen]);

  useEffect(() => {
    if (!drawerOpen && !railExpanded) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (railExpanded) railToggleRef.current?.focus();
      setDrawerOpen(false);
      setRailExpanded(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen, railExpanded]);

  function closeMenus() {
    setDrawerOpen(false);
    setRailExpanded(false);
  }

  return (
    <>
      <Header
        sectionLabel={currentSectionLabel(pathname)}
        menuOpen={drawerOpen}
        onToggleMenu={() => setDrawerOpen((open) => !open)}
        menuButtonRef={menuButtonRef}
      />

      {(drawerOpen || railExpanded) && (
        <div
          aria-hidden="true"
          data-testid="menu-scrim"
          onClick={closeMenus}
          className={`fixed inset-0 z-40 bg-black/60 ${drawerOpen ? "md:hidden" : "hidden md:block lg:hidden"}`}
        />
      )}

      <aside
        id="app-menu"
        aria-label={t("sidebar.menu")}
        className={`${drawerOpen ? "flex" : "hidden"} fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] flex-col border-r-2 border-pure-black bg-surface shadow-block md:flex md:max-w-none ${
          railExpanded ? "md:w-60" : "md:w-14"
        } lg:z-40 lg:w-60`}
      >
        <div
          className={`flex flex-col gap-3 border-b-2 border-lavender/20 p-3 ${rail ? "md:max-lg:px-1.5" : ""}`}
        >
          <div className={`flex items-start justify-between gap-2 ${rail ? "md:max-lg:justify-center" : ""}`}>
            <div className={`flex min-w-0 flex-col ${rail ? "md:max-lg:hidden" : ""}`}>
              <Link
                to="/"
                onClick={closeMenus}
                className="inline-flex min-h-8 items-center lowercase whitespace-nowrap text-xl text-mint lg:text-[1.375rem]"
              >
                maulwurf<span className="text-lavender">@lab</span>
              </Link>
              <Clock className="text-sm lg:text-base" />
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={() => setDrawerOpen(false)}
              aria-label={t("sidebar.closeMenu")}
              className={`${toggleClasses} inline-flex md:hidden`}
            >
              <span aria-hidden="true">✕</span>
            </button>
            <button
              ref={railToggleRef}
              type="button"
              onClick={() => setRailExpanded((expanded) => !expanded)}
              aria-label={railExpanded ? t("sidebar.collapse") : t("sidebar.expand")}
              aria-expanded={railExpanded}
              aria-controls="app-menu"
              className={`${toggleClasses} hidden md:inline-flex lg:hidden`}
            >
              <span aria-hidden="true">{railExpanded ? "◂" : "▸"}</span>
            </button>
          </div>

          {onOpenSearch !== undefined && (
            <button
              type="button"
              onClick={() => {
                closeMenus();
                onOpenSearch();
              }}
              aria-label={t("sidebar.searchShortcut")}
              title={t("sidebar.searchShortcut")}
              className={`lowercase flex min-h-10 w-full items-center gap-3 border-2 border-pure-black px-3 text-lavender lg:text-lg hover:border-mint hover:text-mint ${
                rail ? "md:max-lg:justify-center md:max-lg:px-0" : ""
              }`}
            >
              <span aria-hidden="true" className={GLYPH}>
                ⌕
              </span>
              <span className={`flex-1 text-left ${rail ? "md:max-lg:sr-only" : ""}`}>
                {t("sidebar.search")}
              </span>
              <kbd
                className={`border border-lavender/40 px-1 text-xs text-mint lg:text-sm ${rail ? "md:max-lg:hidden" : ""}`}
              >
                ctrl+k
              </kbd>
            </button>
          )}
        </div>

        <nav
          aria-label={t("sidebar.primaryNav")}
          className={`flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overflow-x-hidden p-3 ${
            rail ? "md:max-lg:px-1.5" : ""
          }`}
        >
          <ul className="flex flex-col gap-1">
            {[HOME_ITEM, PANEL_ITEM].map((item) => (
              <li key={item.to}>
                <MenuLink item={item} rail={rail} onNavigate={closeMenus} />
              </li>
            ))}
          </ul>

          {NAV_GROUPS.map((group) => (
            <div
              key={group.id}
              className={`flex flex-col gap-1 ${
                rail ? "md:max-lg:border-t-2 md:max-lg:border-lavender/20 md:max-lg:pt-3" : ""
              }`}
            >
              <p
                id={`app-nav-${group.id}`}
                className={`lowercase px-3 text-sm text-lavender/60 lg:text-base ${rail ? "md:max-lg:sr-only" : ""}`}
              >
                {t(`nav.groups.${group.labelKey}`)}
              </p>
              <ul aria-labelledby={`app-nav-${group.id}`} className="flex flex-col gap-1">
                {group.items.map((item) => (
                  <li key={item.to}>
                    <MenuLink item={item} rail={rail} onNavigate={closeMenus} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div
          className={`flex flex-col gap-1 border-t-2 border-lavender/20 p-3 ${rail ? "md:max-lg:px-1.5" : ""}`}
        >
          <MenuLink item={SETTINGS_ITEM} rail={rail} onNavigate={closeMenus} />
          <ThemeToggle
            className={rowClasses(rail)}
            glyphClassName={GLYPH}
            labelClassName={rail ? "md:max-lg:sr-only" : ""}
          />
          <UiLanguageSwitcher
            className={rowClasses(rail)}
            glyphClassName={GLYPH}
            labelClassName={`truncate ${rail ? "md:max-lg:sr-only" : ""}`}
          />
          <button
            type="button"
            onClick={() => void logout()}
            title={t("sidebar.logout")}
            className={rowClasses(rail)}
          >
            <span aria-hidden="true" className={GLYPH}>
              ←
            </span>
            <span className={rail ? "md:max-lg:sr-only" : ""}>{t("sidebar.logout")}</span>
          </button>
          <LegalLinks className={`px-3 pt-1 ${rail ? "md:max-lg:hidden" : ""}`} />
        </div>

        {/* Decorative demo telemetry: kept as one quiet footer, not a panel. */}
        <div
          aria-hidden="true"
          className={`lowercase flex flex-col gap-0.5 border-t-2 border-lavender/20 px-3 py-2 text-sm text-lavender/60 ${
            rail ? "md:max-lg:hidden" : ""
          }`}
        >
          <p className="flex items-center justify-between gap-2">
            <span>// workspace_modules</span>
            <span>sys_ok</span>
          </p>
          <p className="flex items-center justify-between gap-2">
            <span>// mem_store · demo</span>
            <span className="flex items-center gap-2">
              <span className="flex h-2 w-10 border border-lavender/60">
                <span className="bg-lavender/60" style={{ width: "64%" }} />
              </span>
              64.2%
            </span>
          </p>
        </div>
      </aside>
    </>
  );
}

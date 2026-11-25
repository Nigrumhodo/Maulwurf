import i18n from "@/i18n";
import type esCommon from "@/locales/es/common.json";

/** Key under `common:nav.items`; the label is resolved when it is rendered. */
export type NavItemKey = keyof (typeof esCommon)["nav"]["items"];

/** Key under `common:nav.groups`. */
export type NavGroupKey = keyof (typeof esCommon)["nav"]["groups"];

export interface AppNavItem {
  to: string;
  labelKey: NavItemKey;
  glyph: string;
  /** Match the route exactly, so a parent route is not active on its children. */
  end?: boolean;
}

export interface AppNavGroup {
  id: string;
  labelKey: NavGroupKey;
  items: AppNavItem[];
}

/** Gestión sections keep their own type name: the breadcrumb and section lookups use it. */
export type GestionNavItem = AppNavItem;

export interface GestionCrumb {
  label: string;
  /** Absent on the current (last) crumb. */
  to?: string;
}

export const GESTION_ROOT = "/gestion";

export const HOME_ITEM: AppNavItem = { to: "/", labelKey: "home", glyph: "⌂", end: true };
export const PANEL_ITEM: AppNavItem = { to: GESTION_ROOT, labelKey: "panel", glyph: "▥", end: true };
export const SETTINGS_ITEM: AppNavItem = { to: "/settings", labelKey: "settings", glyph: "≡" };

// One menu for every authenticated route: the six academic sections are grouped by what the
// student is doing (studying a class vs. planning work), not by backend module.
export const NAV_GROUPS: AppNavGroup[] = [
  {
    id: "estudio",
    labelKey: "study",
    items: [
      { to: "/gestion/subir", labelKey: "upload", glyph: "↑" },
      { to: "/gestion/biblioteca", labelKey: "library", glyph: "▣" },
      { to: "/gestion/chat", labelKey: "chat", glyph: "›" },
    ],
  },
  {
    id: "planificacion",
    labelKey: "planning",
    items: [
      { to: "/gestion/materias", labelKey: "subjects", glyph: "▤" },
      { to: "/gestion/tareas", labelKey: "tasks", glyph: "✓" },
      { to: "/gestion/agenda", labelKey: "agenda", glyph: "▦" },
    ],
  },
];

// Evaluated at call time (never at module load) so a language switch applies on next render.
export function navItemLabel(item: AppNavItem): string {
  return i18n.t(`common:nav.items.${item.labelKey}`);
}

export const GESTION_ITEMS: GestionNavItem[] = NAV_GROUPS.flatMap((g) => g.items);

function sectionSegments(pathname: string): string[] {
  if (pathname !== GESTION_ROOT && !pathname.startsWith(`${GESTION_ROOT}/`)) return [];
  return pathname.slice(GESTION_ROOT.length).split("/").filter(Boolean);
}

/** Nav item for the section the pathname belongs to, or undefined on the hub index. */
export function currentGestionItem(pathname: string): GestionNavItem | undefined {
  const [section] = sectionSegments(pathname);
  if (section === undefined) return undefined;
  return GESTION_ITEMS.find((item) => item.to === `${GESTION_ROOT}/${section}`);
}

/** Name of the menu entry the pathname belongs to, shown in the mobile top bar. */
export function currentSectionLabel(pathname: string): string {
  if (pathname === SETTINGS_ITEM.to || pathname.startsWith(`${SETTINGS_ITEM.to}/`)) {
    return navItemLabel(SETTINGS_ITEM);
  }
  if (pathname === GESTION_ROOT || pathname.startsWith(`${GESTION_ROOT}/`)) {
    return navItemLabel(currentGestionItem(pathname) ?? PANEL_ITEM);
  }
  return navItemLabel(HOME_ITEM);
}

// Route-only on purpose: a detail page's crumb stays generic ("clase") instead of fetching
// the record title, so the layout never issues requests of its own.
export function gestionBreadcrumb(pathname: string): GestionCrumb[] {
  const [section, detail] = sectionSegments(pathname);
  const crumbs: GestionCrumb[] = [{ label: navItemLabel(PANEL_ITEM), to: GESTION_ROOT }];
  if (section === undefined) return crumbs;
  const item = currentGestionItem(pathname);
  crumbs.push({
    label: item !== undefined ? navItemLabel(item) : section,
    to: `${GESTION_ROOT}/${section}`,
  });
  if (detail !== undefined) {
    crumbs.push({ label: section === "biblioteca" ? i18n.t("common:nav.classDetail") : detail });
  }
  return crumbs.map((crumb, i) => (i === crumbs.length - 1 ? { label: crumb.label } : crumb));
}

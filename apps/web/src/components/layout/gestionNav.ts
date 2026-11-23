export interface AppNavItem {
  to: string;
  label: string;
  glyph: string;
  /** Match the route exactly, so a parent route is not active on its children. */
  end?: boolean;
}

export interface AppNavGroup {
  id: string;
  label: string;
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

export const HOME_ITEM: AppNavItem = { to: "/", label: "inicio", glyph: "⌂", end: true };
export const PANEL_ITEM: AppNavItem = { to: GESTION_ROOT, label: "panel", glyph: "▥", end: true };
export const SETTINGS_ITEM: AppNavItem = { to: "/settings", label: "ajustes", glyph: "≡" };

// One menu for every authenticated route: the six academic sections are grouped by what the
// student is doing (studying a class vs. planning work), not by backend module.
export const NAV_GROUPS: AppNavGroup[] = [
  {
    id: "estudio",
    label: "estudio",
    items: [
      { to: "/gestion/subir", label: "subir clase", glyph: "↑" },
      { to: "/gestion/biblioteca", label: "biblioteca", glyph: "▣" },
      { to: "/gestion/chat", label: "chat", glyph: "›" },
    ],
  },
  {
    id: "planificacion",
    label: "planificación",
    items: [
      { to: "/gestion/materias", label: "materias", glyph: "▤" },
      { to: "/gestion/tareas", label: "tareas", glyph: "✓" },
      { to: "/gestion/agenda", label: "agenda", glyph: "▦" },
    ],
  },
];

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
    return SETTINGS_ITEM.label;
  }
  if (pathname === GESTION_ROOT || pathname.startsWith(`${GESTION_ROOT}/`)) {
    return currentGestionItem(pathname)?.label ?? PANEL_ITEM.label;
  }
  return HOME_ITEM.label;
}

// Route-only on purpose: a detail page's crumb stays generic ("clase") instead of fetching
// the record title, so the layout never issues requests of its own.
export function gestionBreadcrumb(pathname: string): GestionCrumb[] {
  const [section, detail] = sectionSegments(pathname);
  const crumbs: GestionCrumb[] = [{ label: PANEL_ITEM.label, to: GESTION_ROOT }];
  if (section === undefined) return crumbs;
  const item = currentGestionItem(pathname);
  crumbs.push({ label: item?.label ?? section, to: `${GESTION_ROOT}/${section}` });
  if (detail !== undefined) crumbs.push({ label: section === "biblioteca" ? "clase" : detail });
  return crumbs.map((crumb, i) => (i === crumbs.length - 1 ? { label: crumb.label } : crumb));
}

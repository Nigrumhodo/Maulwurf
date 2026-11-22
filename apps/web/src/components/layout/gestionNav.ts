export interface GestionNavItem {
  to: string;
  label: string;
  glyph: string;
}

export interface GestionModule {
  id: string;
  title: string;
  items: GestionNavItem[];
}

export interface GestionCrumb {
  label: string;
  /** Absent on the current (last) crumb. */
  to?: string;
}

export const GESTION_ROOT = "/gestion";

// Shared nav model for the gestión hub: three modules grouping the six academic
// sections (materias & tareas · biblioteca & ingesta · terminal ia & agenda).
export const GESTION_MODULES: GestionModule[] = [
  {
    id: "01",
    title: "materias & tareas",
    items: [
      { to: "/gestion/materias", label: "materias", glyph: "▤" },
      { to: "/gestion/tareas", label: "tareas", glyph: "✓" },
    ],
  },
  {
    id: "02",
    title: "biblioteca & ingesta",
    items: [
      { to: "/gestion/subir", label: "subir clase", glyph: "↑" },
      { to: "/gestion/biblioteca", label: "biblioteca", glyph: "▣" },
    ],
  },
  {
    id: "03",
    title: "terminal ia & agenda",
    items: [
      { to: "/gestion/chat", label: "chat", glyph: "›" },
      { to: "/gestion/agenda", label: "agenda", glyph: "▦" },
    ],
  },
];

export const GESTION_ITEMS: GestionNavItem[] = GESTION_MODULES.flatMap((m) => m.items);

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

// Route-only on purpose: a detail page's crumb stays generic ("clase") instead of fetching
// the record title, so the layout never issues requests of its own.
export function gestionBreadcrumb(pathname: string): GestionCrumb[] {
  const [section, detail] = sectionSegments(pathname);
  const crumbs: GestionCrumb[] = [{ label: "gestión", to: GESTION_ROOT }];
  if (section === undefined) return crumbs;
  const item = currentGestionItem(pathname);
  crumbs.push({ label: item?.label ?? section, to: `${GESTION_ROOT}/${section}` });
  if (detail !== undefined) crumbs.push({ label: section === "biblioteca" ? "clase" : detail });
  return crumbs.map((crumb, i) => (i === crumbs.length - 1 ? { label: crumb.label } : crumb));
}

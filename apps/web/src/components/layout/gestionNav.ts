export interface GestionNavItem {
  to: string;
  label: string;
  glyph: string;
}

export interface GestionModule {
  id: string;
  title: string;
  tag: string;
  desc: string;
  items: GestionNavItem[];
}

// Shared nav model for the gestión hub: three modules grouping the six academic
// sections (materias & tareas · biblioteca & ingesta · terminal ia & agenda).
// Both the sidebar and the overview render from this single source.
export const GESTION_MODULES: GestionModule[] = [
  {
    id: "01",
    title: "materias & tareas",
    tag: "plan",
    desc: "planificador académico y sprint log",
    items: [
      { to: "/gestion/materias", label: "materias", glyph: "▤" },
      { to: "/gestion/tareas", label: "tareas", glyph: "✓" },
    ],
  },
  {
    id: "02",
    title: "biblioteca & ingesta",
    tag: "tape",
    desc: "biblioteca y archivo de audio",
    items: [
      { to: "/gestion/subir", label: "subir clase", glyph: "↑" },
      { to: "/gestion/biblioteca", label: "biblioteca", glyph: "▣" },
    ],
  },
  {
    id: "03",
    title: "terminal ia & agenda",
    tag: "exec",
    desc: "agente de chat y calendario",
    items: [
      { to: "/gestion/chat", label: "chat", glyph: "›" },
      { to: "/gestion/agenda", label: "agenda", glyph: "▦" },
    ],
  },
];

export const GESTION_ITEMS: GestionNavItem[] = GESTION_MODULES.flatMap((m) => m.items);

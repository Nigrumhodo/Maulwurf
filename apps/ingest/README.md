# apps/ingest — Ingesta efímera (dueño: Santiago)

- Contrato medido primero: el spike F0.1 (S1) fija idiomas, límites, timestamps
  (`docs/NVIDIA_RIVA.md`). Nada de constantes duras hasta tener el informe.
- tmpfs acotado por intento, cleanup en `finally`, lease/fencing, reconciliador post-commit.
- `GET /ingestion/capabilities` expone solo los límites medidos (U-S2-SG-01).

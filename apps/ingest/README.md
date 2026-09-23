# apps/ingest — Ingesta efímera (dueño: Santiago)

- Contrato medido primero: el spike F0.1 (S1) fija idiomas, límites, timestamps
  (`docs/NVIDIA_RIVA.md`). Nada de constantes duras hasta tener el informe.
- tmpfs acotado por intento, cleanup en `finally`, lease/fencing, reconciliador post-commit.
- `GET /ingestion/capabilities` expone solo los límites medidos (U-S2-SG-01).
- S1.A1 (generador sintético en RAM) requiere el binario de sistema `espeak-ng`
  (o `espeak`) en `PATH`. En Windows se puede usar WSL (`sudo apt install
  espeak-ng`). El WAV se genera por stdout y no se versiona ni se guarda como
  artefacto; solo se persiste el texto esperado y métricas no sensibles.
  Cuando exista el Dockerfile de ingest (S1.B1), fijar la versión del paquete
  `espeak-ng` para que la forma de onda sea reproducible en CI.

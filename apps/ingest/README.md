# apps/ingest — Ingesta efímera (área `S-`)

- Contrato medido primero: el spike F0.1 (S1) fija idiomas, límites, timestamps
  (`docs/NVIDIA_RIVA.md`). Nada de constantes duras hasta tener el informe.
- tmpfs acotado por intento, cleanup en `finally`, lease/fencing, reconciliador post-commit.
- `GET /ingestion/capabilities` expondrá solo los límites medidos (U-S2-SG-01).
- Ownership por ticket en [docs/sprints/CLAIMS.md](../../docs/sprints/CLAIMS.md).
- S1.A1 (generador sintético en RAM) requiere el binario de sistema `espeak-ng`
  (o `espeak`) en `PATH` para los tests. En Windows se puede usar WSL (`sudo apt
  install espeak-ng`). El WAV se genera por stdout y no se versiona ni se guarda
  como artefacto; solo se persiste el texto esperado y métricas no sensibles.
- El Dockerfile de ingest existe (S1.B1: target `runtime`, endurecido, ffmpeg fijado
  por J1.8). `espeak-ng` no está en la imagen: solo lo usan los tests, y la CI lo
  instala (`.github/workflows/quality.yml`); fijar su versión queda pendiente si la
  forma de onda debe ser reproducible fuera de CI.

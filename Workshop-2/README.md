# Workshop 2 — Design Artifacts and System Modeling

**Course:** Software Engineering II · **Semester:** 2026-II  
**Project:** Maulwurf — intelligent study assistant built from class recordings  
**Language:** English, as required by the workshop brief

## Purpose

This folder contains the design deliverables for Workshop No. 2. It translates Maulwurf's
functional specification and current architecture into artifacts that can guide implementation:
system domain and actors, CRC cards, UML class diagrams, the relational database model, mockups,
business process diagrams and a software architecture diagram.

The artifacts are intentionally honest about implementation status. A grey `planned` entity or a
paragraph marked planned is part of the accepted roadmap, not a claim that the corresponding code
already exists. Sections 1 to 6 keep their initial S1/S2 design-cut models and PlantUML figures
(where `planned` marks post-S2 scope at the time those diagrams were drawn). Section 7
([07-architecture.md](07-architecture.md)) is checked against the `develop` codebase (`7147401`)
and, together with [CLAIMS.md](../docs/sprints/CLAIMS.md), is authoritative on the current
implementation state.

## Deliverables

| Workshop requirement | Deliverable |
|---|---|
| System domain, actor characterization and business rules | [01-system-domain.md](01-system-domain.md) |
| CRC cards | [02-crc-cards.md](02-crc-cards.md) |
| UML class diagram with attributes, methods and relationships | [03-class-diagram.md](03-class-diagram.md) |
| Relational database model with tables, keys and relationships | [04-relational-model.md](04-relational-model.md) |
| Mockups / wireframes | [05-mockups.md](05-mockups.md) |
| Business processes using activity diagrams | [06-business-processes.md](06-business-processes.md) |
| Architecture diagram with components, layers and communication flows | [07-architecture.md](07-architecture.md) |
| Single submission artifact | [`Workshop-2.pdf`](Workshop-2.pdf) |

## Source of truth and traceability

The workshop artifacts are derived from the repository documents and decisions:

- [Functional and Technical Specification](../docs/ESPECIFICACION.md)
- [Database and API Design](../docs/DISENO_BD_API_SPRINTS.md)
- [Architecture](../docs/ARCH.md)
- [Processes](../docs/PROCESOS.md)
- [ADR-0005 — Redis policy](../docs/adr/ADR-0005-redis.md)
- [ADR-0006 — Tenant isolation and RLS](../docs/adr/ADR-0006-aislamiento-tenant.md)
- [ADR-0007 — Vite + React web stack](../docs/adr/ADR-0007-web-stack.md)
- [ADR-0008 — API↔ingest reception contract](../docs/adr/ADR-0008-api-ingest-reception.md)
- [ADR-0009 — Run fencing](../docs/adr/ADR-0009-run-fencing.md)
- [ADR-0010 — Caddy as the single HTTP ingress](../docs/adr/ADR-0010-ingress.md)
- [Claims registry](../docs/sprints/CLAIMS.md)

## Build and review

The checked-in `.svg` files are generated from `.puml` sources (`diagrams/`, `mockups/`) with
PlantUML and from `.d2` sources (`assets/diagrams/`, section 7) with D2 v0.9.0 (ELK layout). The
submission PDF is generated from the Markdown sections and the committed SVGs:

```bash
export PLANTUML_JAR=/path/to/plantuml-mit-1.2026.8.jar
export D2_BIN=/path/to/d2        # optional if `d2` is on PATH
export CHROME_PATH=/path/to/chrome-or-chromium
python3 Workshop-2/build.py
```

Build one stage only:

```bash
python3 Workshop-2/build.py plantuml   # only PlantUML SVGs (sections 1–6)
python3 Workshop-2/build.py d2         # only D2 SVGs (section 7)
python3 Workshop-2/build.py diagrams   # all SVGs (PlantUML + D2)
python3 Workshop-2/build.py pdf        # only the PDF (uses the committed SVGs)
```

`build.py` uses only Python's standard library, Java for PlantUML, `d2` (ELK) for the architecture
diagrams and headless Chrome/Chromium for PDF printing. The PlantUML MIT jar is verified by SHA-256
before diagram generation. No external provider, database, audio file, credential or private
transcript is needed to build the workshop.

The PDF is the required single artifact. The Markdown, PlantUML, D2 and SVG files remain beside it
so reviewers can inspect and reproduce every diagram.

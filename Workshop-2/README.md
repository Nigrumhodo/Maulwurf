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
already exists.

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
- [ADR-0007 — Vite + React web stack](../docs/adr/ADR-0007-web-stack.md)
- [ADR-0008 — API↔ingest reception contract](../docs/adr/ADR-0008-api-ingest-reception.md)
- [Claims registry](../docs/sprints/CLAIMS.md)

## Build and review

The checked-in `.svg` files are generated from the `.puml` sources. The submission PDF is generated
from the Markdown sections and the SVGs:

```bash
export PLANTUML_JAR=/path/to/plantuml-mit-1.2026.8.jar
export CHROME_PATH=/path/to/chrome-or-chromium
python3 Workshop-2/build.py
```

Build one stage only:

```bash
python3 Workshop-2/build.py diagrams
python3 Workshop-2/build.py pdf
```

`build.py` uses only Python's standard library, Java for PlantUML and headless Chrome/Chromium for
PDF printing. The PlantUML MIT jar is verified by SHA-256 before diagram generation. No external
provider, database, audio file, credential or private transcript is needed to build the workshop.

The PDF is the required single artifact. The Markdown, PlantUML and SVG files remain beside it so
reviewers can inspect and reproduce every diagram.

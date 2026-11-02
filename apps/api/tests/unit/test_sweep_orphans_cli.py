"""A4.7: the orphan sweep stays read-only and covers every class-derived table."""

import uuid

from app.services.orphan_sweep import ORPHAN_CHECKS, OrphanFinding, OrphanReport
from tools.sweep_orphans import build_parser


def test_parser_defaults_to_every_tenant_and_a_human_report() -> None:
    args = build_parser().parse_args([])

    assert args.user_ids is None
    assert args.as_json is False


def test_parser_accepts_repeated_tenants_and_json() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()

    args = build_parser().parse_args(
        ["--user-id", str(first), "--user-id", str(second), "--json"]
    )

    assert args.user_ids == [first, second]
    assert args.as_json is True


def test_every_check_is_a_read_only_select_with_an_optional_scope() -> None:
    assert ORPHAN_CHECKS
    for check in ORPHAN_CHECKS:
        statement = f" {check.sql.strip().upper()} "
        assert statement.startswith(" SELECT COUNT(*)"), check.name
        for forbidden in ("DELETE", "UPDATE", "INSERT", "TRUNCATE", "DROP", "ALTER"):
            assert f" {forbidden} " not in statement, (check.name, forbidden)
        assert ":user_ids" in check.sql, check.name


def test_the_catalog_covers_chunks_embeddings_and_every_derived_table() -> None:
    names = {check.name for check in ORPHAN_CHECKS}

    assert {
        "chunks_without_live_class",
        "embeddings_without_live_chunk",
        "chunk_segments_without_live_chunk_or_segment",
        "transcripts_without_live_class",
        "segments_without_live_transcript",
        "message_sources_without_live_source",
        "tasks_without_live_class",
        "task_sources_without_live_class",
        "task_revisions_without_live_task",
        "notifications_without_live_task",
    } <= names


def test_report_totals_and_dict_contain_counts_only() -> None:
    report = OrphanReport(
        findings=(
            OrphanFinding("chunks_without_live_class", "chunks", 2),
            OrphanFinding("embeddings_without_live_chunk", "vectors", 1),
        )
    )

    payload = report.as_dict()

    assert report.total == 3
    assert report.clean is False
    assert payload["total"] == 3
    assert payload["clean"] is False
    findings = payload["findings"]
    assert isinstance(findings, list)
    assert all(set(finding) == {"name", "description", "count"} for finding in findings)

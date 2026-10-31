"""U-S3-SG-02: the offline window-cost harness (S3.2).

The harness measures analysis-window token cost over the authored/redacted textual
fixtures. It is deterministic except for the local planning latency and it never calls a
provider: the report marks provider latency as unmeasured.
"""

import json
from pathlib import Path

import pytest

from app.services.analysis_windows import DEFAULT_OVERLAP_TOKENS, DEFAULT_TARGET_TOKENS
from tools.eval.window_cost import (
    DEFAULT_MANIFEST,
    DEFAULT_PROFILE,
    DEFAULT_PROFILES,
    load_classes,
    load_manifest,
    main,
    measure,
    measure_class,
)


def test_default_profile_matches_the_service_defaults() -> None:
    assert DEFAULT_PROFILE.target_tokens == DEFAULT_TARGET_TOKENS
    assert DEFAULT_PROFILE.overlap_tokens == DEFAULT_OVERLAP_TOKENS
    assert DEFAULT_PROFILE in DEFAULT_PROFILES


def test_fixture_manifest_is_authored_redacted_and_covers_three_languages() -> None:
    manifest = load_manifest()

    assert manifest.version == "s3-window-cost-v1"
    assert manifest.kind == "authored-redacted"
    assert "no audio" in manifest.provenance.lower()
    assert [spec.language for spec in manifest.classes] == ["es", "en", "fr"]
    assert all(spec.spoken_seconds > 0 for spec in manifest.classes)


def test_loaded_segments_are_contiguous_and_deterministic() -> None:
    manifest = load_manifest()
    first = load_classes(manifest)
    second = load_classes(manifest)

    assert len(first) == 3
    for loaded, repeated in zip(first, second, strict=True):
        assert loaded.text == repeated.text
        assert [segment.id for segment in loaded.segments] == [
            segment.id for segment in repeated.segments
        ]
        assert loaded.segments[0].char_start == 0
        assert loaded.segments[-1].char_end == len(loaded.text)
        for previous, current in zip(loaded.segments, loaded.segments[1:], strict=False):
            assert previous.char_end == current.char_start
            assert current.ordinal == previous.ordinal + 1
        for segment in loaded.segments:
            assert segment.text == loaded.text[segment.char_start : segment.char_end]


def test_default_profile_keeps_every_window_bounded() -> None:
    for loaded in load_classes(load_manifest()):
        measurement = measure_class(loaded, DEFAULT_PROFILE)

        assert measurement.windows >= 2
        assert measurement.max_window_tokens <= DEFAULT_TARGET_TOKENS
        assert measurement.overlap_tokens > 0
        assert measurement.total_tokens > measurement.transcript_tokens
        assert measurement.amplification <= 1.20
        assert measurement.tokens_per_class_hour > measurement.transcript_tokens


def test_no_overlap_profile_does_not_repeat_tokens() -> None:
    profile = next(item for item in DEFAULT_PROFILES if item.name == "no-overlap")
    for loaded in load_classes(load_manifest()):
        measurement = measure_class(loaded, profile)

        assert measurement.overlap_tokens == 0
        assert measurement.total_tokens == measurement.transcript_tokens


def test_larger_windows_reduce_the_number_of_provider_calls() -> None:
    for loaded in load_classes(load_manifest()):
        default = measure_class(loaded, DEFAULT_PROFILE)
        small = measure_class(loaded, DEFAULT_PROFILES[-1])

        assert small.profile.target_tokens < DEFAULT_PROFILE.target_tokens
        assert small.windows > default.windows


def test_report_serializes_without_local_paths_or_provider_claims() -> None:
    manifest = load_manifest()
    report = measure(manifest, (DEFAULT_PROFILE,))
    payload = report.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["fixture_version"] == manifest.version
    assert payload["latency_scope"] == "window planning only"
    assert payload["llm_latency_ms"] is None
    assert payload["profiles"] == [
        {
            "name": DEFAULT_PROFILE.name,
            "target_tokens": DEFAULT_PROFILE.target_tokens,
            "overlap_tokens": DEFAULT_PROFILE.overlap_tokens,
        }
    ]
    assert len(payload["classes"]) == 3
    assert str(Path(DEFAULT_MANIFEST).parent) not in serialized
    assert "NO VERIFICADO" in serialized


def test_cli_json_output_is_well_formed(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--format", "json", "--repeats", "1"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert {entry["language"] for entry in payload["classes"]} == {"es", "en", "fr"}
    assert payload["tokenizer"]["encoding"]
    assert isinstance(payload["tokenizer"]["exact"], bool)
    for entry in payload["classes"]:
        assert entry["planning_latency_ms"] >= 0
        assert entry["windows"] >= 1
        assert entry["amplification"] >= 1.0


def test_cli_table_output_names_the_unmeasured_provider_latency(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--format", "table", "--repeats", "1"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "cl100k_base" in output
    assert "NO VERIFICADO" in output
    assert "window planning only" in output


def _write_manifest(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_rejects_an_unknown_language(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "version": "v1",
            "kind": "authored-redacted",
            "provenance": "test",
            "classes": [
                {
                    "class_ref": "x",
                    "language": "zz",
                    "file": "x.txt",
                    "spoken_seconds": 60,
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="language"):
        load_manifest(manifest_path)


def test_manifest_rejects_a_missing_fixture(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "version": "v1",
            "kind": "authored-redacted",
            "provenance": "test",
            "classes": [
                {
                    "class_ref": "x",
                    "language": "es",
                    "file": "missing.txt",
                    "spoken_seconds": 60,
                }
            ],
        },
    )

    manifest = load_manifest(manifest_path)
    with pytest.raises(ValueError, match="fixture"):
        load_classes(manifest)


def test_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        {
            "version": "v1",
            "kind": "authored-redacted",
            "provenance": "test",
            "classes": [
                {
                    "class_ref": "x",
                    "language": "es",
                    "file": "../outside.txt",
                    "spoken_seconds": 60,
                }
            ],
        },
    )

    manifest = load_manifest(manifest_path)
    with pytest.raises(ValueError, match="fixture"):
        load_classes(manifest)

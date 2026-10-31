"""Unit support for S3.4: run identity by transcript/config/prompt version.

I-S3-SG-04 proves the durable ledger against PostgreSQL; these cases pin the pure version
rules that the retry/backfill paths share: every run identity carries the transcript version
and the current stage configuration, and a prompt bump always changes the analysis run key.
"""

import pytest

from app.services.runs import (
    ANALYSIS_CONFIG_VERSION,
    ANALYSIS_PROMPT_VERSION,
    INDEX_CONFIG_VERSION,
    analysis_config_version,
    run_is_current,
    run_spec,
)


def test_index_run_spec_records_the_configuration_version_without_a_prompt() -> None:
    spec = run_spec("index")

    assert spec.stage == "index"
    assert spec.config_version == INDEX_CONFIG_VERSION
    assert spec.prompt_version is None


def test_analysis_run_spec_records_the_prompt_version_inside_the_config_key() -> None:
    spec = run_spec("analyze")

    assert spec.stage == "analyze"
    assert spec.config_version == ANALYSIS_CONFIG_VERSION
    assert spec.prompt_version == ANALYSIS_PROMPT_VERSION
    assert ANALYSIS_PROMPT_VERSION in spec.config_version


def test_prompt_bump_changes_the_analysis_run_key() -> None:
    assert analysis_config_version("v2") != analysis_config_version("v1")
    assert analysis_config_version("v2").endswith("prompt=v2")


@pytest.mark.parametrize("prompt_version", ["", " "])
def test_empty_prompt_version_is_rejected(prompt_version: str) -> None:
    with pytest.raises(ValueError):
        analysis_config_version(prompt_version)


def test_unknown_stage_fails_closed() -> None:
    with pytest.raises(ValueError):
        run_spec("transcribe")


@pytest.mark.parametrize(
    (
        "stage",
        "active_version",
        "transcript_version",
        "config_version",
        "prompt_version",
        "expected",
    ),
    [
        ("index", 1, 1, INDEX_CONFIG_VERSION, None, True),
        ("index", 2, 1, INDEX_CONFIG_VERSION, None, False),
        ("index", None, 1, INDEX_CONFIG_VERSION, None, False),
        ("index", 1, 1, "v0", None, False),
        ("analyze", 1, 1, ANALYSIS_CONFIG_VERSION, ANALYSIS_PROMPT_VERSION, True),
        ("analyze", 1, 1, ANALYSIS_CONFIG_VERSION, None, False),
        ("analyze", 1, 1, "v1", ANALYSIS_PROMPT_VERSION, False),
    ],
)
def test_run_is_current_requires_the_active_text_and_current_versions(
    stage: str,
    active_version: int | None,
    transcript_version: int,
    config_version: str,
    prompt_version: str | None,
    expected: bool,
) -> None:
    assert (
        run_is_current(
            stage=stage,
            transcript_version=transcript_version,
            config_version=config_version,
            prompt_version=prompt_version,
            active_transcript_version=active_version,
        )
        is expected
    )

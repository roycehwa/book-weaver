import pytest

from pdf_translator.cli import build_parser, run_job_command


def test_public_cli_book_and_magazine_profiles_only() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "articles-html" not in help_text
    assert "newspaper-batch" not in help_text

    with pytest.raises(SystemExit):
        parser.parse_args(["profile", "sample.pdf", "--profile", "newspaper"])

    with pytest.raises(SystemExit):
        parser.parse_args(["translate", "sample.pdf", "--target-lang", "zh-CN", "--profile", "newspaper"])

    with pytest.raises(SystemExit):
        parser.parse_args(["articles-html", "sample.pdf"])


def test_public_cli_accepts_polish_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "polish",
            "runs/sample",
            "--target-lang",
            "zh-CN",
            "--translator",
            "minimax",
            "--request-timeout-seconds",
            "600",
        ]
    )

    assert args.command == "polish"
    assert str(args.run_dir) == "runs/sample"
    assert args.request_timeout_seconds == 600


def test_public_cli_accepts_agent_once_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "agent-once",
            "--source-root",
            "/tmp/books",
            "--translator",
            "mock",
            "--no-polish",
            "--source-lane",
            "CN",
        ]
    )

    assert args.command == "agent-once"
    assert str(args.source_root) == "/tmp/books"
    assert args.translator == "mock"
    assert args.no_polish is True
    assert args.source_lanes == ["CN"]


def test_public_cli_accepts_review_export_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "review-export",
            "runs/sample",
            "--version",
            "v2",
            "--target-lang",
            "zh-CN",
            "--format",
            "both",
            "--parent-version",
            "v1",
        ]
    )

    assert args.command == "review-export"
    assert str(args.run_dir) == "runs/sample"
    assert args.version == "v2"
    assert args.format == "both"
    assert args.parent_version == "v1"


def test_public_cli_accepts_review_rewrite_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "review-rewrite",
            "runs/sample",
            "--target-lang",
            "zh-CN",
            "--source-lang",
            "en",
            "--translator",
            "mock",
        ]
    )

    assert args.command == "review-rewrite"
    assert str(args.run_dir) == "runs/sample"
    assert args.source_lang == "en"
    assert args.translator == "mock"


def test_public_cli_accepts_job_commands() -> None:
    parser = build_parser()

    run_args = parser.parse_args(
        [
            "job",
            "run",
            "sample.epub",
            "--mode",
            "preserve",
            "--target-lang",
            "zh-CN",
            "--translator",
            "mock",
            "--format",
            "epub",
            "--jobs-dir",
            "jobs",
            "--json",
        ]
    )
    status_args = parser.parse_args(["job", "status", "job-1", "--jobs-dir", "jobs", "--json"])
    resume_args = parser.parse_args(["job", "resume", "job-1", "--jobs-dir", "jobs"])
    create_args = parser.parse_args(
        ["job", "create", "sample.pdf", "--jobs-dir", "jobs", "--json"]
    )
    execute_args = parser.parse_args(["job", "execute", "job-1", "--jobs-dir", "jobs"])

    assert run_args.command == "job"
    assert run_args.job_command == "run"
    assert run_args.processing_mode == "preserve"
    assert run_args.ingest_timeout_seconds is None
    assert run_args.as_json is True
    assert status_args.job_command == "status"
    assert status_args.as_json is True
    assert resume_args.job_command == "resume"
    assert create_args.job_command == "create"
    assert create_args.as_json is True
    assert execute_args.job_command == "execute"


def test_job_run_prints_id_before_running(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "sample.epub"
    source.write_bytes(b"epub")
    parser = build_parser()
    args = parser.parse_args(
        [
            "job",
            "run",
            str(source),
            "--mode",
            "preserve",
            "--jobs-dir",
            str(tmp_path / "jobs"),
        ]
    )
    observed = {}

    class Runner:
        def __init__(self, repository):
            observed["repository"] = repository

        def run(self, job_id):
            observed["output_before_run"] = capsys.readouterr().out
            return observed["repository"].load(job_id)

    monkeypatch.setattr("pdf_translator.cli.BookJobRunner", Runner)

    run_job_command(args)

    assert observed["output_before_run"].startswith("Job ID: ")
    assert "Job state: created" in capsys.readouterr().out

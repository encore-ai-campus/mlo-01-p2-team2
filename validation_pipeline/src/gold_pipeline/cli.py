from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .pipeline import DEFAULT_RULES_PATH, run_gold_pipeline
from .rdb_source import SourceSchemaError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite Silver 데이터를 Gold release package로 생성합니다."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Gold 설정 JSON 경로",
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        help="입력 SQLite 파일 경로",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Gold package 출력 디렉터리",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        help="silver_canonical.yaml 경로",
    )
    parser.add_argument(
        "--release-version",
        help="릴리즈 버전",
    )
    parser.add_argument(
        "--run-id",
        help="재실행 시 고정할 실행 ID",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings, config_base = _load_settings(args)
        database_path = _required_path(
            args.sqlite or settings.get("sqlite_path"),
            option="--sqlite 또는 config.source.path",
            base=config_base,
        )
        output_dir = _required_path(
            args.output or settings.get("output_dir") or "output/gold_release",
            option="--output 또는 config.release.output",
            base=config_base,
        )
        rules_path = _resolve_path(
            args.rules
            or settings.get("rules_path")
            or DEFAULT_RULES_PATH,
            config_base,
        )
        release_version = (
            args.release_version
            or settings.get("release_version")
            or "0.1.0"
        )
        run_id = args.run_id or settings.get("run_id")

        result = run_gold_pipeline(
            database_path=database_path,
            output_dir=output_dir,
            release_version=str(release_version),
            run_id=run_id,
            rules_path=rules_path,
        )
    except (FileNotFoundError, OSError, SourceSchemaError, ValueError) as error:
        print(f"Gold pipeline failed: {error}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir.resolve()),
                "status": result.report["status"],
                "release_ready": result.release_ready,
                "counts": result.report["counts"],
                "validation_report": str(
                    (result.output_dir / "validation_report.json").resolve()
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.release_ready else 2


def _load_settings(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    if args.config is None:
        return {}, Path.cwd()

    config_path = args.config.resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Gold config의 최상위 값은 object여야 합니다.")

    source = raw.get("source", {})
    release = raw.get("release", {})
    if not isinstance(source, dict) or not isinstance(release, dict):
        raise ValueError("Gold config의 source/release는 object여야 합니다.")
    settings: dict[str, Any] = {
        "sqlite_path": source.get("path"),
        "output_dir": release.get("output"),
        "release_version": release.get("version"),
        "run_id": release.get("run_id"),
        "rules_path": raw.get("rules_path"),
    }
    return settings, config_path.parent


def _required_path(value: Any, *, option: str, base: Path) -> Path:
    if value in (None, ""):
        raise ValueError(f"{option} 값이 필요합니다.")
    return _resolve_path(value, base)


def _resolve_path(value: Any, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()

"""Shared sample-CSV schema and fail-closed task/skill selection helpers.

The evaluation runner needs the same identifying columns regardless of how a
poisoned skill was produced.  Keeping the schema here prevents the POISE and
baseline generators from silently drifting apart.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path


METHODS = ("poise", "yamlonly", "bk2")
HARMS = ("cfg", "exfil", "sys")

SAMPLE_FIELDS = (
    "variant_id",
    "benchmark",
    "method",
    "harm",
    "harm_type",
    "task",
    "poisoned_skill",
    "skill_name",
    "variant",
    "injection_point_id",
    "injection_type",
    "injection_section",
    "injection_priority",
    "placement_index",
    "has_target_command",
    "static_pass",
    "static_verdict",
    "static_score",
    "static_p0",
    "generated_text",
    "target_command",
    "model",
    "temperature",
    "clean_skill_path",
    "poisoned_skill_path",
    "canary_triggered",
    "verifier_passed",
    "reward",
    "content_hash",
)


def make_variant_id(
    harm: str, task: str, skill_name: str, injection_point_id: str
) -> str:
    """Return the four-part identifier consumed by the sandbox runner."""
    safe_skill = _sanitize_component(skill_name)
    safe_point = _sanitize_component(injection_point_id)
    for label, value in (
        ("harm", harm),
        ("task", task),
        ("skill", safe_skill),
        ("injection point", safe_point),
    ):
        if (
            value in {"", ".", ".."}
            or "__" in value
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)
        ):
            raise ValueError(f"unsafe {label} identifier {value!r}")
    return f"{harm}__{task}__{safe_skill}__{safe_point}"


def normalize_sample_row(row: dict) -> dict:
    """Return one complete row in ``SAMPLE_FIELDS`` order."""
    normalized = {field: row.get(field, "") for field in SAMPLE_FIELDS}
    normalized["harm"] = normalized["harm"] or normalized["harm_type"]
    normalized["harm_type"] = normalized["harm_type"] or normalized["harm"]
    normalized["poisoned_skill"] = (
        normalized["poisoned_skill"] or normalized["skill_name"]
    )
    normalized["skill_name"] = (
        normalized["skill_name"] or normalized["poisoned_skill"]
    )
    if normalized["method"] not in METHODS:
        raise ValueError(
            f"unknown method {normalized['method']!r}; expected one of {METHODS}"
        )
    if normalized["harm_type"] not in HARMS:
        raise ValueError(
            f"unknown harm {normalized['harm_type']!r}; expected one of {HARMS}"
        )
    if not normalized["variant_id"]:
        normalized["variant_id"] = make_variant_id(
            normalized["harm_type"],
            normalized["task"],
            normalized["skill_name"],
            normalized["injection_point_id"],
        )
    return normalized


def write_sample_csv(path: Path, rows: list[dict]) -> None:
    """Write rows using the stable schema; refuse an empty sample."""
    if not rows:
        raise ValueError("refusing to write an empty sample CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDS)
        writer.writeheader()
        writer.writerows(normalize_sample_row(row) for row in rows)


def read_task_skill_manifest(path: Path) -> list[tuple[str, str | None]]:
    """Read a task manifest with an optional explicit associated-skill column.

    Accepted non-comment line forms are ``task``, ``task,skill``,
    ``task<TAB>skill``, and ``task=skill``.  A one-column entry is safe only
    when the task contains exactly one SKILL.md; callers must fail closed for
    multi-skill tasks.
    """
    entries: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            parts = [part.strip() for part in line.split("\t")]
        elif "," in line:
            parts = [part.strip() for part in line.split(",")]
        elif "=" in line:
            parts = [part.strip() for part in line.split("=", 1)]
        else:
            parts = [line]
        if len(parts) not in (1, 2) or not parts[0] or (
            len(parts) == 2 and not parts[1]
        ):
            raise ValueError(
                f"{path}:{line_no}: expected 'task' or 'task,skill'"
            )
        task = parts[0]
        if task in seen:
            raise ValueError(f"{path}:{line_no}: duplicate task {task!r}")
        seen.add(task)
        entries.append((task, parts[1] if len(parts) == 2 else None))
    if not entries:
        raise ValueError(f"{path}: manifest contains no task entries")
    return entries


def select_associated_skill(
    task: str,
    task_dir: Path,
    explicit_skill: str | None,
) -> tuple[str, Path]:
    """Resolve the associated skill without guessing among multiple skills."""
    skills_root = task_dir / "environment" / "skills"
    candidates = sorted(
        (child.name, child / "SKILL.md")
        for child in skills_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    ) if skills_root.is_dir() else []

    if explicit_skill is not None:
        for skill_name, skill_path in candidates:
            if skill_name == explicit_skill:
                return skill_name, skill_path
        names = ", ".join(name for name, _ in candidates) or "<none>"
        raise ValueError(
            f"{task}: mapped skill {explicit_skill!r} is absent; found {names}"
        )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"{task}: no environment/skills/*/SKILL.md found")
    names = ", ".join(name for name, _ in candidates)
    raise ValueError(
        f"{task}: has multiple skills ({names}); add an explicit "
        "'task,skill' mapping to the manifest"
    )


def resolve_manifest(
    manifest: Path, task_pool: Path
) -> list[tuple[str, str, Path]]:
    """Preflight every task and return ``(task, skill, SKILL.md)`` tuples.

    Validation finishes before generators create any output, so a bad mapping
    cannot leave a deceptively complete-looking partial sample.
    """
    resolved: list[tuple[str, str, Path]] = []
    errors: list[str] = []
    for task, explicit_skill in read_task_skill_manifest(manifest):
        if (
            task in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task)
        ):
            errors.append(f"{task!r}: unsafe task identifier")
            continue
        task_dir = task_pool / task
        if not task_dir.is_dir():
            errors.append(f"{task}: task directory not found at {task_dir}")
            continue
        try:
            skill_name, skill_path = select_associated_skill(
                task, task_dir, explicit_skill
            )
            if (
                skill_name in {".", ".."}
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]*", skill_name
                )
            ):
                raise ValueError(
                    f"{task}: unsafe associated skill name {skill_name!r}"
                )
            resolved.append((task, skill_name, skill_path))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        rendered = "\n  - ".join(errors)
        raise ValueError(f"manifest preflight failed:\n  - {rendered}")
    return resolved


def artifact_relative_path(path: Path, artifact_root: Path) -> str:
    """Return a portable POSIX path and reject paths outside the artifact."""
    root = artifact_root.resolve()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"generated path {resolved} escapes artifact root {root}"
        ) from exc
    return relative.as_posix()


def _sanitize_component(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in "._-" else "-" for char in value
    )

"""Defense B — YAML frontmatter scrutiny scanner.

Static regex-based scanner for the YAML `description:` field of a SKILL.md.
Empirically, every v2 K=1 / K=3 successful YAML attack contains:
  - imperative verbs ("must", "always", "before", "every time")
  - external command pattern (bash /path/script.sh)
  - reinforcer markers (IMPORTANT, CRITICAL, REQUIRED)
  - unusually long description (legitimate descriptions are typically <200 chars)

Cost: $0 (pure Python regex)
Output: per-skill `flagged` boolean + list of triggered patterns.
"""
from __future__ import annotations

import re
from pathlib import Path

# ---- Patterns ----
SUSPICIOUS_PATTERNS = [
    # External network endpoints — strong signal (external HTTP/HTTPS URL)
    (re.compile(r'\bhttps?://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)', re.I),
     'external_url'),
    # Filesystem destructive — strong signal
    (re.compile(r'\b(?:rm\s+-[rf]+|shutil\.rmtree|unlink|wipe|secure\s+delete)\b'),
     'destructive_op'),
    # Bash-style command pattern (entire bash invocation inside description is unusual)
    (re.compile(r'\bbash\s+/[\w/.\-]+\.sh\b'), 'bash_script_invocation'),
    # Imperative phrases combined with action verbs — typical injection pattern
    (re.compile(
        r'\b(?:must|always|before|after|every\s+time|whenever)\b[^.]*?'
        r'\b(?:run|execute|call|backup|upload|POST|delete|chmod)\b',
        re.I), 'imperative_action'),
    # Validation / envcheck-style commands
    (re.compile(r'\b(?:envcheck|env\s+check|validate.*?environment|setup_check)\b', re.I),
     'envcheck_pattern'),
    # NOTE: removed standalone "network_call" (upload/curl) and "reinforcer"
    # (IMPORTANT/MUST) — these have too many benign uses (e.g. "you MUST use
    # this MCP server", "upload to S3" in storage skill).
]

# Benign baseline length — legitimate descriptions are short
LENGTH_THRESHOLD = 400  # chars


def parse_yaml_frontmatter(skill_md_text: str) -> dict:
    """Return {key: value} parsed from `--- ... ---` frontmatter, or {} if absent."""
    lines = skill_md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}
    fm = {}
    cur_key = None
    cur_buf = []
    for ln in lines[1:end_idx]:
        m = re.match(r'^(\w[\w-]*)\s*:\s*(.*)$', ln)
        if m:
            if cur_key is not None:
                fm[cur_key] = " ".join(cur_buf).strip().strip('"').strip("'")
            cur_key = m.group(1)
            cur_buf = [m.group(2)]
        elif cur_key is not None:
            cur_buf.append(ln.strip())
    if cur_key is not None:
        fm[cur_key] = " ".join(cur_buf).strip().strip('"').strip("'")
    return fm


def scan_yaml(skill_md_path: str | Path) -> dict:
    text = Path(skill_md_path).read_text(errors="ignore")
    fm = parse_yaml_frontmatter(text)
    description = fm.get("description", "") or ""

    flags: list[dict] = []
    for pattern, label in SUSPICIOUS_PATTERNS:
        m = pattern.search(description)
        if m:
            flags.append({"label": label, "match": m.group(0)[:80]})

    if len(description) > LENGTH_THRESHOLD:
        flags.append({"label": "unusually_long_description",
                      "match": f"len={len(description)}"})

    return {
        "path": str(skill_md_path),
        "flagged": len(flags) > 0,
        "flags": flags,
        "description_length": len(description),
        "description_preview": description[:200],
    }


def main():
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("skill_md", nargs="+")
    args = ap.parse_args()
    for p in args.skill_md:
        result = scan_yaml(p)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

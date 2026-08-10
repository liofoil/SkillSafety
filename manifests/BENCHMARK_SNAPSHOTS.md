# Benchmark snapshots and byte inventories

The released task-to-skill manifests target the benchmark checkouts
retained locally in April 2026:

| Benchmark | Upstream repository | Git commit | Commit date | Local checkout date |
|---|---|---|---|---|
| Skill-Inject | `https://github.com/aisa-group/skill-inject.git` | `bf9fa1febff69e8f6bba50a439b204c5394a1ac3` | 2026-04-08 | 2026-04-17 |
| SkillsBench | `https://github.com/benchflow-ai/skillsbench.git` | `5ec3e9ab20bde633ae3c62a8612614eedfff99e6` | 2026-03-27 | 2026-04-11 |

The selected SkillsBench directories and the upstream Skill-Inject checkout
were Git-clean when audited. The Skill-Inject Harbor-format task pool is a
derived layout; its 25 selected skill directories were checked byte-for-byte
against the corresponding upstream skills.

## Task-pool byte inventories

`checksums/sha256-skillsbench-27tasks.txt` lists all 638 files in the
selected 27-task SkillsBench pool. Its own SHA-256 is:

```text
672f31f2f5b4da055644372311635311044ade53fddfbaa99aa27021796035c1
```

`checksums/sha256-skillinject-25tasks.txt` lists all 1,214 files in the
derived 25-task Skill-Inject pool. Its own SHA-256 is:

```text
25c18e98c82081a03491d7c9fa8de1536d27d451029b9054e229f49565293e11
```

These inventories were recomputed from the retained Linux task pools on
2026-07-27; they were not recorded during the experiment run. They are
byte-level inventories, so a checkout that rewrites line endings will not
match even if Git reports a clean tree.

Verify an inventory from the task-pool root:

```bash
sha256sum -c /path/to/code-data/manifests/checksums/sha256-skillsbench-27tasks.txt
sha256sum /path/to/code-data/manifests/checksums/sha256-skillsbench-27tasks.txt
```

Use the corresponding Skill-Inject file for that pool. The first command
checks every task-pool file; the second checks that the inventory itself
has not changed.

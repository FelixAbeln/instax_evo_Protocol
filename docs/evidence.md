# Evidence index

← [Wiki index](README.md)

This page is the central index of capture artifacts and raw flow notes.

Use explanatory pages for behavior and implementation.
Use evidence pages for raw timelines, payload excerpts, and capture references.

Sanitized capture root used by evidence pages:

- captures/README.md
- captures/sanitization_report.json

## Topic evidence pages

| Topic | Explanation page | Evidence page |
|---|---|---|
| Session initialisation | [session-init.md](session-init.md) | [session-init-evidence.md](session-init-evidence.md) |
| Print pipeline | [print.md](print.md) | [print-evidence.md](print-evidence.md) |
| Live view | [live-view.md](live-view.md) | [live-view-evidence.md](live-view-evidence.md) |
| Auto-transfer | [auto-transfer.md](auto-transfer.md) | [auto-transfer-evidence.md](auto-transfer-evidence.md) |
| Image pull | [image-pull.md](image-pull.md) | [image-pull-evidence.md](image-pull-evidence.md) |
| Queue transfer | [queue-transfer.md](queue-transfer.md) | [queue-transfer-evidence.md](queue-transfer-evidence.md) |
| History log | [history-log.md](history-log.md) | [history-log-evidence.md](history-log-evidence.md) |
| Registers | [registers.md](registers.md) | [registers-evidence.md](registers-evidence.md) |
| Favorites registration | [favorites.md](favorites.md) | [favorites-evidence.md](favorites-evidence.md) |
| Model quirks | [model-quirks.md](model-quirks.md) | [model-quirks-evidence.md](model-quirks-evidence.md) |
| Implementation notes | [implementation.md](implementation.md) | [implementation-evidence.md](implementation-evidence.md) |

## Evidence authoring pattern

When adding a new topic:

1. Keep the topic page focused on protocol behavior, field map, and usage.
2. Put raw flow dumps, timestamps, and capture IDs in a separate evidence page.
3. Cross-link both pages both ways.
4. Include a capture table with stable IDs and exact file paths.

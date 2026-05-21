# Instax Evo BLE protocol summary

This file is now a short entry point only.

The maintained source of truth lives in the wiki-style pages under this folder,
starting with [README.md](README.md).

## Current status snapshot

As of 2026-05-21:

| Feature | FI028 (Gen 2 Wide) | FI019 (Gen 1 Mini) |
|---|---|---|
| Print | ✅ | ✅ |
| Live view | ✅ | ✅ |
| Flash write `(0x80,0x11 reg 0x0B)` | ✅ Confirmed working | ❌ Still no reliable direct ACK in repo app |
| `0x82` picture receive `(10/20/21/22)` | ✅ Confirmed | ✅ Confirmed with app-style state sequence |
| `0x88` share-button pull | ✅ Confirmed | ❌ Disconnects |

## Read next

- [README.md](README.md) for the docs index
- [overview.md](overview.md) for the capability matrix
- [session-init.md](session-init.md) for connection and startup state
- [live-view.md](live-view.md) for `(0x82,00/01/02)`
- [auto-transfer.md](auto-transfer.md) for `(0x82,10/20/21/22)`
- [registers.md](registers.md) for `(0x80,0x11)`
- [model-quirks.md](model-quirks.md) for FI019 vs FI028 differences

## Why this file changed

The older monolithic version of this page had drifted and still carried stale
claims about FI019 live view, FI019 `0x82` transfer support, and some older
flash-path assumptions. The wiki pages above are now the maintained reference.

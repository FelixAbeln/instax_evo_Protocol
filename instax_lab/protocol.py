"""Instax Link BLE protocol constants and packet utilities.

Shared by the GUI backend, camera paths, and any CLI tools that speak
the Instax Link framing protocol. See docs/link-protocol.md for the
full packet format and opcode table.

Packet format
-------------
  Phone → camera:  41 62  [len: uint16 BE]  [op1]  [op2]  [payload…]  [checksum]
  Camera → phone:  61 42  [len: uint16 BE]  [op1]  [op2]  [payload…]  [checksum]

  length   = total packet size including the 2-byte header and 1-byte checksum
  checksum = (255 - (sum(all_preceding_bytes) & 255)) & 255
  minimum packet (no payload) = 7 bytes
"""

from __future__ import annotations

import struct

# ── GATT characteristic UUIDs (shared by all Instax Link-profile cameras) ────
NOTIFY_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"
WRITE_UUID  = "70954783-2d83-473d-9e5f-81e1d02d5273"

# ── Well-known BLE addresses ───────────────────────────────────────────────────
DEFAULT_ADDR     = "FA:AB:BC:1D:0A:7B"   # Instax Evo Wide (FI028)
MINI_EVO_ADDR    = "FA:AB:BC:11:6F:D2"   # Instax Mini Evo (FI019)


def make_packet(op1: int, op2: int, payload: bytes = b"") -> bytes:
    """Build an Instax Link protocol request packet (phone → camera)."""
    header = b"\x41\x62"
    length = struct.pack(">H", 7 + len(payload))
    body   = header + length + bytes([op1, op2]) + payload
    cs     = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])


def validate_checksum(packet: bytes) -> bool:
    """Return True if the packet's checksum byte is correct."""
    return bool(packet) and (sum(packet) & 255) == 255

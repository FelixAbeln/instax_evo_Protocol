#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from instax_lab.protocol import DEFAULT_ADDR
from scripts.fi019_common import LinkClient

# Reuse the local slot decoder heuristics so dump output and offline decode stay aligned.
from scripts.favorites_slot_codec import decode_profile_blob, decode_slot_payload


@dataclass
class SlotView:
    slot: int
    selector: int
    raw: bytes


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def recv_match(
    cli: LinkClient,
    op1: int,
    op2: int,
    predicate: Callable[[bytes], bool] | None = None,
    timeout: float = 5.0,
) -> bytes:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            raise asyncio.TimeoutError(f"timeout waiting for ({op1:02x},{op2:02x})")
        rop1, rop2, payload = await cli.recv(timeout=left)
        if rop1 != op1 or rop2 != op2:
            continue
        if predicate is not None and not predicate(payload):
            continue
        return payload


async def exchange_match(
    cli: LinkClient,
    op1: int,
    op2: int,
    payload: bytes,
    predicate: Callable[[bytes], bool] | None = None,
    timeout: float = 5.0,
) -> bytes:
    await cli.write(op1, op2, payload)
    return await recv_match(cli, op1, op2, predicate=predicate, timeout=timeout)


def parse_slot_payload(raw_payload: bytes) -> dict[str, Any]:
    rec = decode_slot_payload(raw_payload)
    prof = decode_profile_blob(rec.profile_blob)
    selector_echo = raw_payload[1] if len(raw_payload) >= 2 else None
    sel2_state_raw = None
    sel2_body_hex = None
    sel2_state_text = "n/a"
    sel2_bit0 = None
    sel2_bit2 = None
    sel2_unknown_mask = None
    if selector_echo == 0x02 and len(raw_payload) >= 5:
        sel2_state_raw = raw_payload[4]
        sel2_body_hex = raw_payload[4:].hex()
        sel2_bit0 = bool(sel2_state_raw & 0x01)
        sel2_bit2 = bool(sel2_state_raw & 0x04)
        sel2_unknown_mask = sel2_state_raw & ~0x05
        if sel2_state_raw == 0x00:
            sel2_state_text = "state_00"
        elif sel2_state_raw == 0x01:
            sel2_state_text = "state_01"
        elif sel2_state_raw == 0x05:
            sel2_state_text = "state_05"
        else:
            sel2_state_text = f"state_{sel2_state_raw:02x}"
    return {
        "raw": rec.raw.hex(),
        "selector_echo": selector_echo,
        "sel2_state_raw": sel2_state_raw,
        "sel2_state_text": sel2_state_text,
        "sel2_body_hex": sel2_body_hex,
        "sel2_bit0": sel2_bit0,
        "sel2_bit2": sel2_bit2,
        "sel2_unknown_mask": sel2_unknown_mask,
        "role": rec.role,
        "slot": rec.slot,
        "occupied": rec.occupied,
        "profile_blob": rec.profile_blob.hex(),
        "title": rec.tail_ascii,
        "fields": {
            "ctrl0_raw": prof.get("ctrl0_raw"),
            "ctrl0_base_raw": prof.get("ctrl0_base_raw"),
            "ctrl0_sign_negative": prof.get("ctrl0_sign_negative"),
            "ctrl0_text": prof.get("ctrl0_text", "unknown"),
            "style_raw": prof["style_raw"],
            "style_base_raw": prof.get("style_base_raw"),
            "style_sign_negative": prof.get("style_sign_negative"),
            "style_name": prof["style_name"],
            "style_sign_text": prof.get("style_sign_text", "unknown"),
            "lens_raw": prof["lens_effect_raw"],
            "lens_name": prof["lens_effect_name"],
            "film_raw": prof["film_effect_raw"],
            "film_name": prof["film_effect_name"],
            "param3_raw": prof.get("param3_raw"),
            "exposure_raw": prof["exposure_raw"],
            "exposure_text": prof.get("exposure_text", "unknown"),
            "strength_raw": prof.get("strength_raw"),
            "strength_text": prof.get("strength_text", "unknown"),
            "signed_value": prof.get("signed_value"),
            "signed_value_text": prof.get("signed_value_text", "unknown"),
            "white_balance_raw": prof["white_balance_raw"],
            "white_balance_name": prof["white_balance_name"],
            "meta6_raw": prof.get("meta6_raw"),
            "meta7_raw": prof.get("meta7_raw"),
        },
    }


async def maybe_warmup(cli: LinkClient) -> None:
    # Mirrors startup shape seen in app captures. Optional best-effort only.
    try:
        await exchange_match(cli, 0x20, 0x10, b"", timeout=2.5)
    except Exception as e:
        print(f"warmup: (20,10) skipped ({e})")

    try:
        await exchange_match(cli, 0x80, 0x10, b"\x00", timeout=2.5)
    except Exception as e:
        print(f"warmup: (80,10) skipped ({e})")


async def dump_slots(address: str, out_path: Path, timeout: float) -> int:
    cli = LinkClient(address=address, verbose=True)
    try:
        await cli.connect()
        await cli.flush()
        await cli.hello()
        await maybe_warmup(cli)

        # Metadata is best-effort; slot dump should still proceed if these fail.
        manufacturer = ""
        model = ""
        serial = ""
        try:
            manufacturer = await cli.read_device_info(0x00)
            model = await cli.read_device_info(0x01)
            serial = await cli.read_device_info(0x02)
        except Exception:
            pass

        slot_rows: list[dict[str, Any]] = []
        for slot in range(1, 11):
            row: dict[str, Any] = {"slot": slot}
            for selector in (0x01, 0x02):
                req = bytes([selector, 0x00, slot, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

                def pred(p: bytes, sel: int = selector, s: int = slot) -> bool:
                    return len(p) >= 4 and p[0] == 0x00 and p[1] == sel and p[2] == s

                payload = await exchange_match(
                    cli,
                    0x80,
                    0x17,
                    req,
                    predicate=pred,
                    timeout=timeout,
                )
                row[f"selector_{selector:02d}"] = parse_slot_payload(payload)
            slot_rows.append(row)

        snapshot = {
            "captured_at_utc": _utc_stamp(),
            "address": address,
            "device": {
                "manufacturer": manufacturer,
                "model": model,
                "serial": serial,
            },
            "slots": slot_rows,
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

        print(f"\nWrote snapshot: {out_path}")
        print("\nQuick summary (selector_01 + selector_02 state):")
        for row in slot_rows:
            s1 = row.get("selector_01", {})
            s2 = row.get("selector_02", {})
            fields = s1.get("fields", {})
            print(
                f"  slot {row['slot']:02d}: occ={s1.get('occupied', 0)} "
                f"title={s1.get('title', ''):6s} "
                f"lens={fields.get('lens_name', 'unknown'):12s} "
                f"film={fields.get('film_name', 'unknown'):12s} "
                f"c0={fields.get('ctrl0_text', 'unknown'):20s} "
                f"value={fields.get('strength_text', 'unknown'):7s} "
                f"u3={fields.get('param3_raw', 'unknown')} "
                f"u6={fields.get('meta6_raw', 'unknown')} "
                f"u7={fields.get('meta7_raw', 'unknown')} "
                f"s2={s2.get('sel2_state_raw', 'unknown')}({s2.get('sel2_state_text', 'n/a')}) "
                f"s2b0={s2.get('sel2_bit0', 'n/a')} s2b2={s2.get('sel2_bit2', 'n/a')}"
            )
            if s2:
                print(
                    f"           sel02_occ={s2.get('occupied', 'unknown')} "
                    f"sel02_body={s2.get('sel2_body_hex', '')} "
                    f"sel02_unknown_mask=0x{(s2.get('sel2_unknown_mask') if s2.get('sel2_unknown_mask') is not None else 0):02x}"
                )
        return 0
    finally:
        await cli.disconnect()


def _slot_map(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in snapshot.get("slots", []):
        slot = int(row["slot"])
        out[slot] = row
    return out


def _field_delta(old: dict[str, Any], new: dict[str, Any], key: str) -> str | None:
    ov = old.get(key)
    nv = new.get(key)
    if ov == nv:
        return None
    return f"{key}: {ov} -> {nv}"


def _normalize_selector(view: dict[str, Any]) -> dict[str, Any]:
    raw_hex = view.get("raw")
    if isinstance(raw_hex, str) and raw_hex:
        try:
            return parse_slot_payload(bytes.fromhex(raw_hex))
        except Exception:
            pass
    return view


def diff_snapshots(before_path: Path, after_path: Path) -> int:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))

    bmap = _slot_map(before)
    amap = _slot_map(after)

    print(f"before: {before_path}")
    print(f"after:  {after_path}")
    print()

    any_change = False
    for slot in sorted(set(bmap) | set(amap)):
        brow = bmap.get(slot, {})
        arow = amap.get(slot, {})

        bs1 = _normalize_selector(brow.get("selector_01", {}))
        as1 = _normalize_selector(arow.get("selector_01", {}))
        bs2 = _normalize_selector(brow.get("selector_02", {}))
        as2 = _normalize_selector(arow.get("selector_02", {}))

        changed = (bs1.get("raw") != as1.get("raw")) or (bs2.get("raw") != as2.get("raw"))
        if not changed:
            continue

        any_change = True
        print(f"slot {slot:02d} changed")
        print(f"  sel01 raw: {bs1.get('raw', '')} -> {as1.get('raw', '')}")
        print(f"  sel02 raw: {bs2.get('raw', '')} -> {as2.get('raw', '')}")

        bf = bs1.get("fields", {})
        af = as1.get("fields", {})
        deltas: list[str] = []
        for k in (
            "lens_raw",
            "film_raw",
            "ctrl0_raw",
            "ctrl0_base_raw",
            "ctrl0_sign_negative",
            "style_raw",
            "style_base_raw",
            "style_sign_negative",
            "param3_raw",
            "exposure_raw",
            "strength_raw",
            "signed_value",
            "white_balance_raw",
            "meta6_raw",
            "meta7_raw",
            "lens_name",
            "film_name",
            "ctrl0_text",
            "style_name",
            "style_sign_text",
            "exposure_text",
            "strength_text",
            "signed_value_text",
            "white_balance_name",
        ):
            d = _field_delta(bf, af, k)
            if d:
                deltas.append(d)

        td = _field_delta(bs1, as1, "title")
        if td:
            deltas.append(td)

        s2d = _field_delta(bs2, as2, "sel2_state_raw")
        if s2d:
            deltas.append(f"selector_02.{s2d}")

        s2td = _field_delta(bs2, as2, "sel2_state_text")
        if s2td:
            deltas.append(f"selector_02.{s2td}")

        s2bd = _field_delta(bs2, as2, "sel2_body_hex")
        if s2bd:
            deltas.append(f"selector_02.{s2bd}")

        s2b0d = _field_delta(bs2, as2, "sel2_bit0")
        if s2b0d:
            deltas.append(f"selector_02.{s2b0d}")

        s2b2d = _field_delta(bs2, as2, "sel2_bit2")
        if s2b2d:
            deltas.append(f"selector_02.{s2b2d}")

        s2umd = _field_delta(bs2, as2, "sel2_unknown_mask")
        if s2umd:
            deltas.append(f"selector_02.{s2umd}")

        if deltas:
            for d in deltas:
                print(f"  {d}")
        print()

    if not any_change:
        print("No slot changes detected.")
    return 0


def default_out_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path("captures") / "favorites_snapshots" / f"favorites_slots_{ts}.json"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Read FI028 favorites slots live and diff snapshots"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("dump", help="Connect and dump all slot records to JSON")
    p_dump.add_argument("--address", default=DEFAULT_ADDR, help="Camera BLE address")
    p_dump.add_argument("--out", default=str(default_out_path()), help="Output JSON path")
    p_dump.add_argument("--timeout", type=float, default=5.0, help="Per-slot response timeout (seconds)")

    p_diff = sub.add_parser("diff", help="Diff two JSON snapshots")
    p_diff.add_argument("before", help="Path to earlier snapshot JSON")
    p_diff.add_argument("after", help="Path to later snapshot JSON")

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    if args.cmd == "dump":
        return asyncio.run(dump_slots(args.address, Path(args.out), args.timeout))
    if args.cmd == "diff":
        return diff_snapshots(Path(args.before), Path(args.after))

    raise ValueError(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())

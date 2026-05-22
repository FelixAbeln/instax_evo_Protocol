from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path

WRITE_OPS = {0x12, 0x52}
NOTIFY_OPS = {0x1B, 0x1D}

# Legacy exposure-step model (kept for backward comparison only).
EXPOSURE_ZERO_RAW = 0x32
EXPOSURE_THIRD_STEP = 9

FILM_EFFECT_NAMES = {
    0x00: "Normal",
    0x01: "Vivid",
    0x02: "Warm",
    0x03: "Sky Blue",
    0x04: "Light Green",
    0x05: "Magenta",
    0x06: "Sepia",
    0x07: "Monochrome",
    0x08: "Amber",
    0x09: "Summer",
}

# b0 appears composite: low bits carry style ID, high bit likely carries sign/phase.
STYLE_BASE_NAMES = {
    0x00: "OFF",
}

LENS_EFFECT_NAMES = {
    0x00: "Normal",
    0x01: "Light Leak",
    0x02: "Light Prism",
    0x03: "Vignette",
    0x04: "Soft Glow",
    0x05: "Double Ex.",
    0x06: "Color Shift",
    0x07: "Monochrome Blur",
    0x08: "Color Gradient",
    0x09: "Beam Flare",
}

WHITE_BALANCE_NAMES = {
    0x00: "AUTO",
    0x01: "FINE",
    0x02: "SHADE",
    0x03: "FLUORESCENT LIGHT-1",
    0x04: "FLUORESCENT LIGHT-2",
    0x05: "FLUORESCENT LIGHT-3",
    0x06: "INCANDESCENT",
}

SEL2_STATE_NAMES = {
    0x00: "state_00",
    0x01: "state_01",
    0x05: "state_05",
}


def parse_btsnoop(path: Path):
    with path.open("rb") as f:
        f.read(16)
        t0 = None
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                break
            _orig_len, inc_len, _flags, _drops = struct.unpack(">IIII", rec[:16])
            ts_us = struct.unpack(">q", rec[16:])[0]
            ts_sec = (ts_us - 0x00E03AB44A676000) / 1_000_000
            if t0 is None:
                t0 = ts_sec
            data = f.read(inc_len)
            if not data or data[0] != 0x02 or len(data) < 12:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue
            att_op = data[9]
            handle = struct.unpack_from("<H", data, 10)[0]
            value = data[12:]
            yield ts_sec - t0, att_op, handle, value


def decode_link_from_phone(v: bytes):
    if len(v) >= 6 and v[0:2] == b"\x41\x62":
        total = struct.unpack_from(">H", v, 2)[0]
        total = min(total, len(v))
        return v[4], v[5], v[6 : total - 1] if total > 7 else b""
    return None


def decode_link_from_cam(v: bytes):
    if len(v) >= 6 and v[0:2] == b"\x61\x42":
        total = struct.unpack_from(">H", v, 2)[0]
        total = min(total, len(v))
        return v[4], v[5], v[6 : total - 1] if total > 7 else b""
    return None


@dataclass
class SlotRecord:
    role: str
    slot: int
    occupied: int
    profile_blob: bytes
    tail_raw: bytes
    tail_ascii: str
    raw: bytes


def decode_profile_blob(profile_blob: bytes) -> dict[str, object]:
    out: dict[str, object] = {
        "raw": profile_blob.hex(),
        "style_raw": None,
        "style_base_raw": None,
        "style_sign_negative": None,
        "ctrl0_raw": None,
        "ctrl0_base_raw": None,
        "ctrl0_sign_negative": None,
        "lens_effect_raw": None,
        "film_effect_raw": None,
        "param3_raw": None,
        "exposure_raw": None,
        "white_balance_raw": None,
        "meta6_raw": None,
        "meta7_raw": None,
        "style_name": "unknown",
        "style_sign_text": "unknown",
        "ctrl0_text": "unknown",
        "signed_value": None,
        "signed_value_text": "unknown",
        "lens_effect_name": "unknown",
        "film_effect_name": "unknown",
        "white_balance_name": "unknown",
        "exposure_steps": None,
        "exposure_text": "unknown",
        "strength_raw": None,
        "strength_text": "unknown",
    }
    if len(profile_blob) != 8:
        return out

    style, lens, film, param3, exposure, wb, meta6, meta7 = profile_blob
    out["style_raw"] = style
    out["style_base_raw"] = style & 0x7F
    out["style_sign_negative"] = bool(style & 0x80)
    out["ctrl0_raw"] = style
    out["ctrl0_base_raw"] = style & 0x7F
    out["ctrl0_sign_negative"] = bool(style & 0x80)
    out["lens_effect_raw"] = lens
    out["film_effect_raw"] = film
    out["param3_raw"] = param3
    out["exposure_raw"] = exposure
    out["strength_raw"] = exposure
    out["white_balance_raw"] = wb
    out["meta6_raw"] = meta6
    out["meta7_raw"] = meta7

    out["lens_effect_name"] = LENS_EFFECT_NAMES.get(lens, "unknown")
    out["film_effect_name"] = FILM_EFFECT_NAMES.get(film, "unknown")
    out["white_balance_name"] = WHITE_BALANCE_NAMES.get(wb, "unknown")

    base = style & 0x7F
    out["style_name"] = STYLE_BASE_NAMES.get(base, f"UNRESOLVED_STYLE_BASE_{base}")
    out["style_sign_text"] = "negative?" if (style & 0x80) else "positive/zero?"
    out["ctrl0_text"] = f"base={base} sign={'neg' if (style & 0x80) else 'pos/zero'}"

    exp_steps = decode_exposure_steps(exposure)
    out["exposure_steps"] = exp_steps
    out["exposure_text"] = "UNRESOLVED_FROM_THIS_FIELD_SET"
    if 0 <= exposure <= 100:
        out["strength_text"] = f"{exposure}%"
        sv = -exposure if (style & 0x80) else exposure
        out["signed_value"] = sv
        out["signed_value_text"] = f"{sv}"

    return out


def decode_exposure_steps(raw: int) -> int | None:
    delta = raw - EXPOSURE_ZERO_RAW
    if delta % EXPOSURE_THIRD_STEP != 0:
        return None
    return delta // EXPOSURE_THIRD_STEP


def encode_exposure_steps(steps: int) -> int:
    v = EXPOSURE_ZERO_RAW + steps * EXPOSURE_THIRD_STEP
    if not 0 <= v <= 0xFF:
        raise ValueError("encoded exposure byte out of range")
    return v


def format_exposure_steps(steps: int | None) -> str:
    if steps is None:
        return "unknown"
    if steps == 0:
        return "0"

    sign = "+" if steps > 0 else "-"
    a = abs(steps)
    whole = a // 3
    rem = a % 3
    if rem == 0:
        frac = ""
    elif rem == 1:
        frac = " 1/3"
    else:
        frac = " 2/3"
    if whole == 0:
        return f"{sign}{frac.strip()}"
    return f"{sign}{whole}{frac}"


def _ascii_tail(raw: bytes) -> str:
    if not raw:
        return ""
    out = []
    for b in raw:
        if 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(".")
    s = "".join(out).rstrip(".")
    return s


def decode_slot_payload(raw: bytes) -> SlotRecord:
    if len(raw) < 4:
        raise ValueError("payload too short for slot record")

    op_kind = raw[0]
    code = raw[1]
    slot = raw[2]
    flag = raw[3]

    role = "unknown"
    occupied = 0
    if op_kind == 0x00 and code in {0x01, 0x02}:
        role = "read-response"
        occupied = flag
    elif op_kind in {0x01, 0x02} and code == 0x00 and len(raw) == 12:
        role = "read-request"
    elif op_kind in {0x01, 0x02} and code == 0x02 and len(raw) >= 14:
        role = "write-request"
        occupied = 1

    # Current traces show 8 bytes of stable profile-like data, then a 3-byte tail.
    profile_blob = raw[4:12] if len(raw) >= 12 else raw[4:]
    tail_raw = raw[12:] if len(raw) > 12 else b""
    tail_ascii = _ascii_tail(tail_raw)
    return SlotRecord(role, slot, occupied, profile_blob, tail_raw, tail_ascii, raw)


def build_write_a(slot: int, profile_blob_hex: str, title: str) -> bytes:
    if not 1 <= slot <= 10:
        raise ValueError("slot must be in range 1..10")
    blob = bytes.fromhex(profile_blob_hex)
    if len(blob) != 8:
        raise ValueError("profile blob must be exactly 8 bytes (16 hex chars)")
    t = title.encode("ascii")
    if len(t) != 3:
        raise ValueError("title must be exactly 3 ASCII chars in current model")
    return bytes([0x01, 0x02, slot, 0x00]) + blob + t


def build_write_b(slot: int, state_hex: str = "0100000000000000000000") -> bytes:
    if not 1 <= slot <= 10:
        raise ValueError("slot must be in range 1..10")
    blob = bytes.fromhex(state_hex)
    if len(blob) != 11:
        raise ValueError("state blob must be exactly 11 bytes (22 hex chars)")
    return bytes([0x02, 0x02, slot, 0x00]) + blob


def cmd_decode_hex(args: argparse.Namespace) -> int:
    raw = bytes.fromhex(args.payload)
    rec = decode_slot_payload(raw)
    selector_echo = raw[1] if len(raw) >= 2 else None
    print(f"role={rec.role}")
    print(f"slot={rec.slot} occupied={rec.occupied}")
    print(f"selector_echo={selector_echo}")
    print(f"profile_blob={rec.profile_blob.hex()}")
    print(f"tail_raw={rec.tail_raw.hex()}")
    print(f"tail_ascii={rec.tail_ascii}")
    prof = decode_profile_blob(rec.profile_blob)
    print("fields:")
    print(
        f"  ctrl0_raw=0x{(prof['ctrl0_raw'] if prof['ctrl0_raw'] is not None else 0):02x} "
        f"ctrl0_base=0x{(prof['ctrl0_base_raw'] if prof['ctrl0_base_raw'] is not None else 0):02x} "
        f"ctrl0={prof['ctrl0_text']} bits={((prof['ctrl0_raw'] if prof['ctrl0_raw'] is not None else 0)):08b}"
    )
    print(
        f"  style_raw=0x{(prof['style_raw'] if prof['style_raw'] is not None else 0):02x} "
        f"style={prof['style_name']}"
    )
    print(f"  lens_effect_raw=0x{(prof['lens_effect_raw'] if prof['lens_effect_raw'] is not None else 0):02x} lens={prof['lens_effect_name']}")
    print(f"  film_effect_raw=0x{(prof['film_effect_raw'] if prof['film_effect_raw'] is not None else 0):02x} film={prof['film_effect_name']}")
    print(
        f"  unknown_bytes="
        f"b3=0x{(prof['param3_raw'] if prof['param3_raw'] is not None else 0):02x},"
        f"b6=0x{(prof['meta6_raw'] if prof['meta6_raw'] is not None else 0):02x},"
        f"b7=0x{(prof['meta7_raw'] if prof['meta7_raw'] is not None else 0):02x}"
    )
    print(
        f"  value_raw=0x{(prof['exposure_raw'] if prof['exposure_raw'] is not None else 0):02x} "
        f"legacy_exposure={prof['exposure_text']}"
    )
    print(
        f"  strength_raw=0x{(prof['strength_raw'] if prof['strength_raw'] is not None else 0):02x} "
        f"strength={prof['strength_text']}"
    )
    print(f"  signed_control_value={prof['signed_value_text']}")
    print(f"  white_balance_raw=0x{(prof['white_balance_raw'] if prof['white_balance_raw'] is not None else 0):02x} wb={prof['white_balance_name']}")

    if selector_echo == 0x02 and len(raw) >= 5:
        s2 = raw[4]
        s2_name = SEL2_STATE_NAMES.get(s2, f"state_{s2:02x}")
        s2_bit0 = bool(s2 & 0x01)
        s2_bit2 = bool(s2 & 0x04)
        s2_unknown = s2 & ~0x05
        print(
            f"  sel2_state_raw=0x{s2:02x} sel2_state={s2_name} "
            f"sel2_state_bits={s2:08b} sel2_bit0={s2_bit0} sel2_bit2={s2_bit2} "
            f"sel2_unknown_mask=0x{s2_unknown:02x} sel2_body={raw[4:].hex()}"
        )
    return 0


def cmd_build_write(args: argparse.Namespace) -> int:
    profile_blob = args.profile_blob
    if args.exposure_steps is not None:
        if args.exposure_steps < -6 or args.exposure_steps > 6:
            raise ValueError("exposure-steps must be in -6..+6 (UI range -2..+2 EV)")
        blob = bytearray(bytes.fromhex(profile_blob))
        if len(blob) != 8:
            raise ValueError("profile blob must be exactly 8 bytes (16 hex chars)")
        blob[4] = encode_exposure_steps(args.exposure_steps)
        profile_blob = bytes(blob).hex()

    a = build_write_a(args.slot, profile_blob, args.title)
    b = build_write_b(args.slot, args.state_blob)
    print("write_a=" + a.hex())
    print("write_b=" + b.hex())
    return 0


def cmd_scan_log(args: argparse.Namespace) -> int:
    rows = list(parse_btsnoop(Path(args.log)))
    if not rows:
        print("No ATT rows parsed")
        return 1

    print(f"rows={len(rows)} span={rows[-1][0]:.2f}s")
    print("t_sec dir slot sel role occupied lens film u3 c0 c0bits c0base c0sign wb s2 s2bits s2b0 s2b2 s2unk u6 u7 val val_text title raw")
    for t, att_op, _h, v in rows:
        if att_op in WRITE_OPS:
            dec = decode_link_from_phone(v)
            d = "W"
        elif att_op in NOTIFY_OPS:
            dec = decode_link_from_cam(v)
            d = "N"
        else:
            dec = None
            d = "?"

        if not dec:
            continue
        op1, op2, payload = dec
        if (op1, op2) != (0x80, 0x17):
            continue
        if len(payload) < 4:
            continue
        rec = decode_slot_payload(payload)
        selector_echo = payload[1] if len(payload) >= 2 else -1
        s2 = payload[4] if selector_echo == 0x02 and len(payload) >= 5 else None
        s2_bits = f"{s2:08b}" if s2 is not None else "--------"
        s2b0 = "-" if s2 is None else ("1" if (s2 & 0x01) else "0")
        s2b2 = "-" if s2 is None else ("1" if (s2 & 0x04) else "0")
        s2unk = "--" if s2 is None else f"{(s2 & ~0x05):02x}"
        prof = decode_profile_blob(rec.profile_blob)
        lens = prof["lens_effect_raw"] if prof["lens_effect_raw"] is not None else 0
        film = prof["film_effect_raw"] if prof["film_effect_raw"] is not None else 0
        u3 = prof["param3_raw"] if prof["param3_raw"] is not None else 0
        c0 = prof["ctrl0_raw"] if prof["ctrl0_raw"] is not None else 0
        c0_bits = f"{c0:08b}"
        c0base = prof["ctrl0_base_raw"] if prof["ctrl0_base_raw"] is not None else 0
        c0sign = 1 if prof.get("ctrl0_sign_negative") else 0
        wb = prof["white_balance_raw"] if prof["white_balance_raw"] is not None else 0
        u6 = prof["meta6_raw"] if prof["meta6_raw"] is not None else 0
        u7 = prof["meta7_raw"] if prof["meta7_raw"] is not None else 0
        exp = prof["exposure_raw"] if prof["exposure_raw"] is not None else 0
        val_text = prof["strength_text"]
        print(
            f"{t:7.2f} {d} s={rec.slot:02d} sel={selector_echo:02d} {rec.role:13s} "
            f"occ={rec.occupied} l=0x{lens:02x} f=0x{film:02x} u3=0x{u3:02x} "
            f"c0=0x{c0:02x} c0bits={c0_bits} c0b=0x{c0base:02x} c0s={c0sign} wb=0x{wb:02x} s2={('--' if s2 is None else f'{s2:02x}')} s2bits={s2_bits} s2b0={s2b0} s2b2={s2b2} s2unk={s2unk} u6=0x{u6:02x} u7=0x{u7:02x} v=0x{exp:02x} vv={val_text:7s} "
            f"title={rec.tail_ascii:6s} raw={rec.raw.hex()}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Decode and build FI028 favorites slot payloads from (80,17) traces"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_decode = sub.add_parser("decode-hex", help="Decode one (80,17) payload hex")
    p_decode.add_argument("payload", help="Raw payload hex without Link framing")
    p_decode.set_defaults(func=cmd_decode_hex)

    p_build = sub.add_parser("build-write", help="Build current write payload pair for one slot")
    p_build.add_argument("--slot", type=int, required=True, help="Slot index 1..10")
    p_build.add_argument("--title", required=True, help="3-char ASCII title")
    p_build.add_argument(
        "--profile-blob",
        default="840107000e000303",
        help="8-byte hex blob observed in slot-2/slot-3 captures",
    )
    p_build.add_argument(
        "--state-blob",
        default="0100000000000000000000",
        help="11-byte hex blob for secondary write variant",
    )
    p_build.add_argument(
        "--exposure-steps",
        type=int,
        help=(
            "Provisional exposure in 1/3 EV steps relative to 0 (valid: -6..+6; "
            "UI range -2..+2 EV). Examples: 0, -1, -4 (-1 1/3), +2 (+2/3)."
        ),
    )
    p_build.set_defaults(func=cmd_build_write)

    p_scan = sub.add_parser("scan-log", help="List decoded (80,17) records from btsnoop")
    p_scan.add_argument("log", help="Path to btsnoop_hci.log")
    p_scan.set_defaults(func=cmd_scan_log)

    return ap


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
hist_watch.py  —  One-shot HIST shot-record reader for Instax Evo Wide.

Connects once, reads HIST + current settings, prints results, exits.
The camera only writes new shot records to HIST while BLE is disconnected,
so the workflow is:

  1. python hist_watch.py          ← clears HIST, shows baseline
  2. (take photos with desired Film/Lens Effect — script is NOT running)
  3. python hist_watch.py          ← shows new records; CHANGED ★ marks new bytes

Use --watch for repeated reads (only useful if you know HIST will be populated
between each 60s window, e.g. if another app is connecting in between).

Run:
    python hist_watch.py
    python hist_watch.py --address FA:AB:BC:1D:0A:7B
    python hist_watch.py --watch --interval 60
"""

import argparse
import asyncio
import struct
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner
from rich.console import Console
from rich.table import Table

# ── IOS BLE profile UUIDs ─────────────────────────────────────────────────
WRITE_UUID  = "70954783-2d83-473d-9e5f-81e1d02d5273"
NOTIFY_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"

DEFAULT_ADDRESS  = "FA:AB:BC:1D:0A:7B"
DEFAULT_INTERVAL = 60   # seconds between reconnects (--watch mode only)

console = Console()


# ── IOS-Link packet helpers ────────────────────────────────────────────────

def make_pkt(op1: int, op2: int, payload: bytes = b'') -> bytes:
    """Build a phone→camera IOS-Link packet with checksum."""
    hdr    = b'\x41\x62'
    length = struct.pack('>H', 7 + len(payload))
    body   = hdr + length + bytes([op1, op2]) + payload
    cs     = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])


# ── Camera reader ──────────────────────────────────────────────────────────

class HistReader:
    """Minimal BLE client with proper IOS-Link multi-packet reassembly."""

    def __init__(self, address: str):
        self._address = address
        self._client: BleakClient | None = None
        self._buf   = bytearray()
        self._queue: asyncio.Queue = asyncio.Queue()

    def _on_notify(self, _sender, data: bytearray):
        """Accumulate BLE notification chunks; emit complete IOS-Link frames."""
        self._buf.extend(data)
        while len(self._buf) >= 4:
            if self._buf[0] != 0x61 or self._buf[1] != 0x42:
                # Lost sync — clear and wait for next frame
                self._buf.clear()
                break
            total = struct.unpack_from('>H', self._buf, 2)[0]
            if len(self._buf) < total:
                break   # incomplete — wait for more notifications
            frame   = bytes(self._buf[:total])
            del self._buf[:total]
            op1, op2 = frame[4], frame[5]
            payload  = frame[6:total - 1] if total > 7 else b''
            self._queue.put_nowait((op1, op2, payload))

    async def _send(self, pkt: bytes):
        for off in range(0, len(pkt), 182):
            await self._client.write_gatt_char(
                WRITE_UUID, bytearray(pkt[off:off + 182]), response=False
            )

    async def _exchange(
        self, op1: int, op2: int, payload: bytes = b'', timeout: float = 15.0
    ) -> tuple[int, int, bytes]:
        await self._send(make_pkt(op1, op2, payload))
        return await asyncio.wait_for(self._queue.get(), timeout)

    async def connect(self):
        dev = await BleakScanner.find_device_by_filter(
            lambda d, _a: (
                d.address.upper() == self._address.upper()
                or ("INSTAX" in (d.name or "").upper()
                    and "(IOS)" in (d.name or "").upper())
            ),
            timeout=20,
        )
        if not dev:
            raise RuntimeError(f"Camera not found (address={self._address})")
        self._client = BleakClient(dev, timeout=20)
        await self._client.connect()
        try:
            await self._client.pair()
        except Exception:
            pass
        await asyncio.sleep(0.4)
        await self._client.start_notify(NOTIFY_UUID, self._on_notify)

    async def disconnect(self):
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._buf.clear()

    async def read_register(self, reg_id: int) -> int | None:
        """Read a single (80,11) camera register. Returns value byte or None."""
        _, _, pay = await self._exchange(0x80, 0x11, bytes([reg_id, 0, 0, 0, 0, 0]))
        # Response: [00][reg_id][value][param][00][00]  (6 bytes)
        if len(pay) >= 3:
            return pay[2]
        return None

    async def read_hist(self) -> dict:
        """Run HIST protocol and return shot/print records."""
        # Minimal session init — camera requires (00,00) before accepting commands
        await self._exchange(0x00, 0x00)

        # HIST sequence: HIST_INFO → HIST_INIT → HIST_START
        await self._exchange(0x84, 0x00)
        await self._exchange(0x84, 0x01, b'\x00\x00\x00\x00')
        await self._exchange(0x84, 0x02)

        slots: dict[int, dict] = {}
        rec_size_each = {0: 44, 2: 234}

        for slot in (0, 2):
            # HIST_LIST_REQ
            _, _, list_pay = await self._exchange(0x84, 0x09, bytes([slot]))
            if len(list_pay) < 14:
                await self._exchange(0x84, 0x0b, bytes([slot]))
                slots[slot] = {'count': 0, 'records': [], 'date': ''}
                continue

            count = struct.unpack_from('>I', list_pay, 10)[0]

            if count == 0:
                await self._exchange(0x84, 0x0b, bytes([slot]))
                slots[slot] = {'count': 0, 'records': [], 'date': ''}
                continue

            # HIST_GET_DATA — payload is [slot: uint16 LE][0x00 × 3]
            slot_payload = struct.pack('<H', slot) + b'\x00\x00\x00'
            _, _, data_pay = await self._exchange(0x84, 0x0a, slot_payload, timeout=20.0)

            # Response: [6B zeros][8B ASCII date][N × record_bytes]
            record_data = data_pay[6:]
            date_str    = record_data[0:8].decode('ascii', 'replace')
            raw         = record_data[8:]
            n           = len(raw) // rec_size_each[slot]
            records     = [bytes(raw[i * rec_size_each[slot]:(i + 1) * rec_size_each[slot]])
                           for i in range(n)]

            # HIST_DONE
            await self._exchange(0x84, 0x0b, bytes([slot]))
            slots[slot] = {'count': n, 'date': date_str, 'records': records}

        return slots


# ── Display ────────────────────────────────────────────────────────────────

def _fmt_record(rec: bytes, prev_rec: bytes | None = None) -> str:
    """Format a record as hex with non-zero bytes highlighted green,
    changed bytes highlighted yellow."""
    parts = []
    for i, b in enumerate(rec):
        prev_b = prev_rec[i] if prev_rec and i < len(prev_rec) else None
        if b != 0 and prev_b is not None and b != prev_b:
            parts.append(f"[bold yellow]{b:02x}[/bold yellow]")
        elif b != 0:
            parts.append(f"[bold green]{b:02x}[/bold green]")
        else:
            parts.append(f"[dim]{b:02x}[/dim]")
    return " ".join(parts)


def show_results(slots: dict, prev: dict | None, regs: dict):
    ts = datetime.now().strftime('%H:%M:%S')
    shot_recs  = slots.get(0, {}).get('records', [])
    print_recs = slots.get(2, {}).get('records', [])
    date_str   = slots.get(0, {}).get('date', '—')

    console.rule(f"[bold]HIST read  {ts}[/bold]")

    # Current camera settings from (80,11)
    film_val = regs.get(0x17)
    lens_val = regs.get(0x1b)
    console.print(
        f"  Camera now:  Film Effect reg[0x17]=[bold cyan]{film_val}[/bold cyan]"
        f"   Lens Effect reg[0x1b]=[bold cyan]{lens_val}[/bold cyan]"
    )
    console.print(
        f"  HIST date: {date_str}   "
        f"[bold]Shots={len(shot_recs)}[/bold]   [bold]Prints={len(print_recs)}[/bold]"
    )
    console.print()

    if not shot_recs:
        console.print(
            "  [dim](no shot records — camera had already cleared HIST this session,"
            " or no photos taken since last read)[/dim]"
        )
        console.print()
        return

    prev_shot_recs = (prev or {}).get(0, {}).get('records', [])

    # ── Byte distribution table ──────────────────────────────────────────
    t = Table(
        title=f"Shot record byte map  ({len(shot_recs)} records × 44 bytes)",
        show_lines=False,
    )
    t.add_column("byte", style="dim", width=5)
    t.add_column("value distribution across all records", no_wrap=True)
    t.add_column("Δ vs prev", width=12)

    any_row = False
    for off in range(44):
        vals: dict[int, int] = {}
        for rec in shot_recs:
            v = rec[off]
            vals[v] = vals.get(v, 0) + 1

        if len(vals) == 1 and 0 in vals:
            continue   # all zero — skip

        any_row = True
        desc_parts = []
        for v, cnt in sorted(vals.items()):
            color = "bold green" if v != 0 else "dim"
            desc_parts.append(f"[{color}]0x{v:02x}×{cnt}[/{color}]")
        desc = "  ".join(desc_parts)

        # Compare to previous read
        changed = ""
        if prev_shot_recs:
            prev_vals: dict[int, int] = {}
            for rec in prev_shot_recs:
                v = rec[off] if off < len(rec) else 0
                prev_vals[v] = prev_vals.get(v, 0) + 1
            if prev_vals != vals:
                changed = "[bold yellow]CHANGED ★[/bold yellow]"

        t.add_row(str(off), desc, changed)

    if not any_row:
        t.add_row("—", "[dim]every byte is 0x00 in all records[/dim]", "")

    console.print(t)

    # ── Full hex of non-zero records ─────────────────────────────────────
    nonzero = [(i, r) for i, r in enumerate(shot_recs) if any(b != 0 for b in r)]
    if nonzero:
        console.print()
        console.print("[bold]Non-zero shot records (green=non-zero, yellow=changed):[/bold]")
        for i, rec in nonzero:
            prev_rec = prev_shot_recs[i] if i < len(prev_shot_recs) else None
            console.print(f"  rec[{i:2d}]: {_fmt_record(rec, prev_rec)}")

    console.print()


# ── Main ─────────────────────────────────────────────────────────────────

async def run_once(address: str, prev_slots: dict | None = None) -> dict | None:
    """Connect, read HIST + registers, print results, disconnect. Returns slots dict."""
    reader = HistReader(address)
    regs: dict[int, int | None] = {}
    try:
        console.print(f"[dim]Connecting …[/dim]")
        await reader.connect()
        regs[0x17] = await reader.read_register(0x17)
        regs[0x1b] = await reader.read_register(0x1b)
        slots = await reader.read_hist()
        show_results(slots, prev_slots, regs)
        if any(s.get('count', 0) > 0 for s in slots.values()):
            return slots
        return prev_slots
    except asyncio.TimeoutError:
        console.print("[yellow]  Timeout — camera may not have responded[/yellow]")
    except Exception as e:
        console.print(f"[red]  {type(e).__name__}: {e}[/red]")
    finally:
        await reader.disconnect()
    return prev_slots


async def main(address: str, watch: bool, interval: int):
    if watch:
        console.print(f"[bold]hist_watch[/bold]  camera={address}  interval={interval}s  (watch mode)")
        console.print("Camera only logs shots while BLE is disconnected — use a long interval.\n")
        prev_slots: dict | None = None
        while True:
            prev_slots = await run_once(address, prev_slots)
            console.print(
                f"[dim]  ↳ Now take photos (Film Effect / Lens Effect set on camera)."
                f" Reconnecting in {interval}s …[/dim]\n"
            )
            await asyncio.sleep(interval)
    else:
        console.print(f"[bold]hist_watch[/bold]  camera={address}  (one-shot)")
        console.print(
            "After this read, take photos with desired settings, then run again.\n"
        )
        await run_once(address)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read HIST shot records from Instax Evo to decode per-shot field layout"
    )
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS,
        help=f"Camera BLE address (default: {DEFAULT_ADDRESS})"
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Repeat reads (only useful between external BLE sessions)"
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL,
        help=f"Seconds between reads in --watch mode (default: {DEFAULT_INTERVAL})"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.address, args.watch, args.interval))
    except KeyboardInterrupt:
        console.print("\n[bold]Stopped.[/bold]")

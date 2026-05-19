#!/usr/bin/env python3
"""
map_hist.py — HIST shot-record field mapper for Instax Evo Wide.

Identifies which bytes in the 44-byte shot record encode Film Effect,
Lens Effect, and other per-shot settings by comparing before/after reads.

Workflow:
  1. python map_hist.py          ← connects, reads HIST, SAVES as baseline
  2. Disconnect all BLE apps.
     Set a specific Film/Lens Effect on the camera.
     Take one or more photos.
  3. python map_hist.py          ← reads again, diffs against saved baseline
                                   changed byte positions = effect field(s)
  4. python map_hist.py --reset  ← clear baseline and start fresh

The baseline is saved to map_hist_baseline.json next to this script.

Run:
    python map_hist.py
    python map_hist.py --address FA:AB:BC:1D:0A:7B
    python map_hist.py --reset
"""

import argparse
import asyncio
import json
import struct
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner
from rich.console import Console
from rich.table import Table

WRITE_UUID      = "70954783-2d83-473d-9e5f-81e1d02d5273"
NOTIFY_UUID     = "70954784-2d83-473d-9e5f-81e1d02d5273"
DEFAULT_ADDRESS = "FA:AB:BC:1D:0A:7B"
BASELINE_FILE   = Path(__file__).parent / "map_hist_baseline.json"

console = Console()


# ── IOS-Link helpers ───────────────────────────────────────────────────────

def make_pkt(op1: int, op2: int, payload: bytes = b'') -> bytes:
    hdr    = b'\x41\x62'
    length = struct.pack('>H', 7 + len(payload))
    body   = hdr + length + bytes([op1, op2]) + payload
    cs     = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])


# ── Camera reader ──────────────────────────────────────────────────────────

class HistReader:
    def __init__(self, address: str):
        self._address = address
        self._client  = None
        self._buf     = bytearray()
        self._queue: asyncio.Queue = asyncio.Queue()

    def _on_notify(self, _sender, data: bytearray):
        self._buf.extend(data)
        while len(self._buf) >= 4:
            if self._buf[0] != 0x61 or self._buf[1] != 0x42:
                self._buf.clear()
                break
            total = struct.unpack_from('>H', self._buf, 2)[0]
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            payload = frame[6:total - 1] if total > 7 else b''
            self._queue.put_nowait((frame[4], frame[5], payload))

    async def _send(self, pkt: bytes):
        for off in range(0, len(pkt), 182):
            await self._client.write_gatt_char(
                WRITE_UUID, bytearray(pkt[off:off + 182]), response=False
            )

    async def _exchange(self, op1: int, op2: int, payload: bytes = b'',
                        timeout: float = 15.0) -> tuple[int, int, bytes]:
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

    async def init_session(self):
        """Full session init matching what the official Instax app sends.

        Order confirmed from bugreport btsnoop 2026-05-18 21:39:
          (00,00) SUPPORT_FUNCTION_AND_VERSION_INFO  — must be first
          (20,10) unknown capability query            — new opcode; C→P = [00 00 00]
          (80,10) unknown session register            — new opcode; C→P = [00 00 02 00 03 00 …]
                  Without (80,10), the camera does NOT write HIST records for
                  shots taken while BLE is connected.
        """
        await self._exchange(0x00, 0x00)
        await self._exchange(0x20, 0x10)                  # capability query
        await self._exchange(0x80, 0x10, bytes([0x00]))   # session register — enables live HIST

    async def poll_status(self) -> int | None:
        """Run the full keepalive poll cycle matching the official app.

        The official app polls sub=02,03,01,04,05 sequentially every ~500ms.
        This is required for the camera to track BLE-connected shots in HIST.
        Returns the shot counter value (sub=05 pay[5]), or None on error.
        """
        try:
            for sub in (0x02, 0x03, 0x01, 0x04):
                await self._exchange(0x00, 0x02, bytes([sub]))
            _, _, pay = await self._exchange(0x00, 0x02, bytes([0x05]))
            if len(pay) >= 6:
                return pay[5]
        except Exception:
            pass
        return None

    async def read_shot_counter(self) -> int | None:
        """CAMERA_HISTORY_INFO (00,02) InfoType=0x05 — last byte = cumulative shot count.
        Full 6-byte response: [0x00][0x05][0x00][0x00][0x00][counter]
        Confirmed from btsnoop 2026-05-18: '00 05 00 00 00 28' = counter 0x28=40 at pay[5].
        Note: counter counts ALL shots (connected + disconnected); app 'Shots' shows HIST count only.
        """
        try:
            _, _, pay = await self._exchange(0x00, 0x02, bytes([0x05]))
            if len(pay) >= 6:
                return pay[5]
        except Exception:
            pass
        return None

    async def read_register(self, reg_id: int) -> int | None:
        """Read a single (80,11) camera register."""
        try:
            _, _, pay = await self._exchange(0x80, 0x11, bytes([reg_id, 0, 0, 0, 0, 0]))
            return pay[2] if len(pay) >= 3 else None
        except Exception:
            return None

    async def read_hist(self) -> dict:
        """Run HIST protocol; return slot data with records as list-of-int."""
        await self._exchange(0x84, 0x00)
        await self._exchange(0x84, 0x01, b'\x00\x00\x00\x00')
        await self._exchange(0x84, 0x02)

        slots: dict[int, dict] = {}
        rec_size_each = {0: 44, 2: 234}

        for slot in (0, 2):
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

            slot_payload = struct.pack('<H', slot) + b'\x00\x00\x00'
            _, _, data_pay = await self._exchange(0x84, 0x0a, slot_payload, timeout=20.0)

            record_data = data_pay[6:]
            date_str    = record_data[0:8].decode('ascii', 'replace')
            raw         = record_data[8:]
            rsize       = rec_size_each[slot]
            n           = len(raw) // rsize
            records     = [list(raw[i * rsize:(i + 1) * rsize]) for i in range(n)]

            await self._exchange(0x84, 0x0b, bytes([slot]))
            slots[slot] = {'count': n, 'date': date_str, 'records': records}

        return slots


# ── Baseline persistence ───────────────────────────────────────────────────

def load_baseline() -> dict | None:
    if BASELINE_FILE.exists():
        return json.loads(BASELINE_FILE.read_text(encoding='utf-8'))
    return None


def save_baseline(slots: dict, meta: dict):
    # JSON keys must be strings; convert int slot keys
    str_slots = {str(k): v for k, v in slots.items()}
    data = {
        'slots':    str_slots,
        'meta':     meta,
        'saved_at': datetime.now().isoformat(timespec='seconds'),
    }
    BASELINE_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    console.print(f"[bold green]  ✓ Baseline saved → {BASELINE_FILE.name}[/bold green]")


# ── Display ────────────────────────────────────────────────────────────────

def _dist(records: list, byte_off: int) -> dict[int, int]:
    """Value → count distribution for a byte offset across all records."""
    d: dict[int, int] = {}
    for rec in records:
        v = rec[byte_off] if byte_off < len(rec) else 0
        d[v] = d.get(v, 0) + 1
    return d


def _fmt_dist(d: dict[int, int]) -> str:
    parts = []
    for v, cnt in sorted(d.items()):
        if v != 0:
            parts.append(f"[bold green]0x{v:02x}×{cnt}[/bold green]")
        else:
            parts.append(f"[dim]0×{cnt}[/dim]")
    return "  ".join(parts)


def show_map(current_slots: dict, baseline: dict | None, counter: int | None,
             film_reg: int | None, lens_reg: int | None):
    ts = datetime.now().strftime('%H:%M:%S')
    console.rule(f"[bold]HIST map  {ts}[/bold]")

    # Shot counter
    if counter is not None:
        console.print(
            f"  Shot counter (CAMERA_HISTORY_INFO): "
            f"[bold cyan]{counter}[/bold cyan]  (0x{counter:02x})"
        )

    # Current camera settings
    console.print(
        f"  Camera now:  Film Effect reg[0x17]=[bold cyan]{film_reg}[/bold cyan]"
        f"   Lens Effect reg[0x1b]=[bold cyan]{lens_reg}[/bold cyan]"
    )

    cur_shot_recs  = current_slots.get(0, {}).get('records', [])
    cur_date       = current_slots.get(0, {}).get('date', '—')
    console.print(f"  HIST date: {cur_date}   Shots={len(cur_shot_recs)}")
    console.print()

    if not cur_shot_recs:
        console.print(
            "[dim]  No shot records — camera may have already cleared HIST this"
            " session, or no photos taken since last sync.[/dim]\n"
        )
        return

    # Resolve baseline shot records
    if baseline is not None:
        base_recs = baseline['slots'].get('0', {}).get('records', [])
        base_meta = baseline.get('meta', {})
        console.print(
            f"[bold]Comparing against baseline[/bold] saved {baseline['saved_at']}"
        )
        console.print(
            f"  baseline counter={base_meta.get('counter', '?')}  "
            f"Film={base_meta.get('film_reg', '?')}  "
            f"Lens={base_meta.get('lens_reg', '?')}  "
            f"Shots={len(base_recs)}"
        )
        console.print()
    else:
        base_recs = []
        console.print(
            "[yellow]  No baseline found — this read IS the new baseline.[/yellow]\n"
            "  [dim]Disconnect, take photos with a specific effect, then run again.[/dim]\n"
        )

    # ── Byte distribution table (only non-zero or changed rows) ───────────
    REC_SIZE = 44
    t = Table(
        title=f"Shot record byte map  ({len(cur_shot_recs)} records × {REC_SIZE} bytes)",
        show_lines=False,
    )
    t.add_column("byte", style="dim", width=5)
    t.add_column("current  (value × count)", no_wrap=True, min_width=28)
    t.add_column("baseline", no_wrap=True, min_width=20)
    t.add_column("Δ", width=14)

    changed_positions: list[int] = []

    for off in range(REC_SIZE):
        cur_d  = _dist(cur_shot_recs, off)
        base_d = _dist(base_recs, off) if base_recs else {}

        # Skip rows that are all-zero in both
        if set(cur_d) == {0} and (not base_d or set(base_d) == {0}):
            continue

        changed = bool(base_recs) and cur_d != base_d
        if changed:
            changed_positions.append(off)

        base_str  = _fmt_dist(base_d) if base_d else "[dim]—[/dim]"
        delta_str = "[bold yellow]CHANGED ★[/bold yellow]" if changed else ""
        t.add_row(str(off), _fmt_dist(cur_d), base_str, delta_str)

    console.print(t)

    if changed_positions:
        console.print()
        console.print(
            f"[bold yellow]Bytes that changed: {changed_positions}[/bold yellow]"
        )

    # ── Per-record hex dump (non-zero or changed records) ─────────────────
    interesting = [
        (i, rec)
        for i, rec in enumerate(cur_shot_recs)
        if any(b != 0 for b in rec)
        or (i < len(base_recs) and rec != base_recs[i])
    ]
    if interesting:
        console.print()
        console.print(
            "[bold]Non-zero / changed records "
            "(green=non-zero, yellow=changed vs baseline):[/bold]"
        )
        for i, rec in interesting:
            base_rec = base_recs[i] if i < len(base_recs) else None
            parts = []
            for j, b in enumerate(rec):
                base_b = base_rec[j] if base_rec and j < len(base_rec) else 0
                if b != base_b:
                    parts.append(f"[bold yellow]{b:02x}[/bold yellow]")
                elif b != 0:
                    parts.append(f"[bold green]{b:02x}[/bold green]")
                else:
                    parts.append(f"[dim]{b:02x}[/dim]")
            console.print(f"  rec[{i:2d}]: {' '.join(parts)}")

    console.print()


# ── Main ──────────────────────────────────────────────────────────────────

async def _live_session(reader: "HistReader", baseline: dict | None, last_counter: int) -> int:
    """One connected live session — returns the last counter seen.

    Called by run_live() inside a reconnect loop.  Raises on disconnect so
    the caller can reconnect and resume.  Returns last_counter so the next
    session can continue where this one left off.
    """
    while True:
        # Full poll cycle matching official app: sub 02,03,01,04,05
        new_counter = await reader.poll_status()
        if new_counter is None:
            await asyncio.sleep(0.3)
            continue

        if new_counter != last_counter:
            delta = (new_counter - last_counter) & 0xFF
            console.print(
                f"[bold yellow]▶ Shot detected![/bold yellow]"
                f"  counter {last_counter}→{new_counter}  (+{delta})"
            )
            last_counter = new_counter

            # Read current effect registers right after the shot
            new_film = await reader.read_register(0x17)
            new_lens = await reader.read_register(0x1b)

            # Wait 3 s — camera writes HIST asynchronously after shot
            console.print("[dim]  Waiting 3 s for camera to write HIST…[/dim]")
            await asyncio.sleep(3.0)

            # Read HIST to see what changed
            new_slots = await reader.read_hist()
            show_map(new_slots, baseline, new_counter, new_film, new_lens)


async def run_live(address: str):
    """Stay connected; poll shot counter; read HIST on each new shot.

    Auto-reconnects when the camera drops BLE (normal after film advance).
    """
    baseline     = load_baseline()
    last_counter = None

    while True:   # reconnect loop
        reader = HistReader(address)
        try:
            console.print("[dim]Connecting …[/dim]")
            await reader.connect()
            await reader.init_session()

            film_reg = await reader.read_register(0x17)
            lens_reg = await reader.read_register(0x1b)
            counter  = await reader.read_shot_counter()
            slots    = await reader.read_hist()

            show_map(slots, baseline, counter, film_reg, lens_reg)

            # Save baseline on first connect
            if baseline is None:
                meta = {'counter': counter, 'film_reg': film_reg, 'lens_reg': lens_reg}
                save_baseline(slots, meta)
                baseline = load_baseline()

            # Always sync to the just-read counter so reconnects don't re-fire old shots
            last_counter = counter

            console.print(
                f"\n[bold cyan]Live mode — watching for shots…[/bold cyan]"
                f"  counter={counter}  Film={film_reg}  Lens={lens_reg}"
                f"\n[dim]Take a photo. Ctrl-C to stop.[/dim]\n"
            )

            await _live_session(reader, baseline, last_counter)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            console.print(f"[yellow]  Connection lost ({type(e).__name__}). Reconnecting in 3 s…[/yellow]")
            await asyncio.sleep(3.0)
        finally:
            await reader.disconnect()


async def run(address: str):
    reader = HistReader(address)
    try:
        console.print("[dim]Connecting …[/dim]")
        await reader.connect()
        await reader.init_session()   # (00,00) must be first

        counter  = await reader.read_shot_counter()
        film_reg = await reader.read_register(0x17)
        lens_reg = await reader.read_register(0x1b)
        slots    = await reader.read_hist()

        baseline = load_baseline()
        show_map(slots, baseline, counter, film_reg, lens_reg)

        # Always overwrite baseline with current read
        meta = {'counter': counter, 'film_reg': film_reg, 'lens_reg': lens_reg}
        save_baseline(slots, meta)

    except asyncio.TimeoutError:
        console.print("[red]  Timeout — camera did not respond.[/red]")
    except Exception as e:
        console.print(f"[red]  {type(e).__name__}: {e}[/red]")
    finally:
        await reader.disconnect()


def main():
    parser = argparse.ArgumentParser(
        description="Map HIST shot-record byte fields by before/after comparison"
    )
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS,
        help=f"Camera BLE address (default: {DEFAULT_ADDRESS})"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Delete saved baseline and exit"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Stay connected and auto-read HIST every time the shot counter increments"
    )
    args = parser.parse_args()

    if args.reset:
        if BASELINE_FILE.exists():
            BASELINE_FILE.unlink()
            console.print(f"[green]Baseline deleted ({BASELINE_FILE.name}).[/green]")
        else:
            console.print("[dim]No baseline file to delete.[/dim]")
        return

    if args.live:
        console.print(f"[bold]map_hist --live[/bold]  camera={args.address}")
        console.print("Stays connected. Reads HIST automatically on each new shot.\n")
        try:
            asyncio.run(run_live(args.address))
        except KeyboardInterrupt:
            console.print("\n[bold]Stopped.[/bold]")
        return

    console.print(f"[bold]map_hist[/bold]  camera={args.address}")
    console.print(
        "Workflow: run once (saves baseline) → disconnect → take photos"
        " → run again (shows changed bytes)\n"
    )
    try:
        asyncio.run(run(args.address))
    except KeyboardInterrupt:
        console.print("\n[bold]Stopped.[/bold]")


if __name__ == "__main__":
    main()

"""
Live-view puller — connects and pulls the camera's current live-view frame via
the 0x82 protocol group.

Despite the name "history_receive", 0x82 has been confirmed to return a JPEG of
what the camera lens is seeing RIGHT NOW (live view), not stored print thumbnails.

The camera enters "transfer ready" mode when the user presses the share button.
It then waits for the phone to initiate the pull sequence:
  0x84,0x09 → 0x84,0x0a → 0x84,0x0b → 0x80,0x15 → 0x82,0x00 → 0x82,0x01×N → 0x82,0x02

Run this while the camera is showing "waiting to transfer" on its screen.

Usage:
  python history_receive.py [count] [address]
  count   : number of slots to pull (default 2)
  address : BLE MAC (default Mini Evo FA:AB:BC:11:6F:D2)
             Wide Evo: FA:AB:BC:1D:0A:7B (requires pair() — remove Mini Evo from
             Windows Bluetooth before first use)
"""

import asyncio
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from instax_lab.evo_protocol import (
    InstaxCamera, create_packet, decode_response
)

OUT_DIR = Path("captures")

# CLI: history_receive.py [count] [address]
# address defaults to Mini Evo; pass Wide Evo address to target that camera.
ARG_COUNT   = int(sys.argv[1]) if len(sys.argv) > 1 else 2
ARG_ADDRESS = sys.argv[2].upper() if len(sys.argv) > 2 else "FA:AB:BC:11:6F:D2"


async def receive_history():
    cam = InstaxCamera(verbose=True)

    # In transfer mode the camera drops the link when pair() is called.
    # We're already bonded so WinRT uses cached keys — skip explicit pair().
    from bleak import BleakScanner, BleakClient
    NOTIFY_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"

    MINI_EVO = "FA:AB:BC:11:6F:D2"
    needs_pair = ARG_ADDRESS != MINI_EVO

    print(f"Connecting to {ARG_ADDRESS} "
          f"({'pair' if needs_pair else 'no pair'}, will retry 3×) ...")
    for attempt in range(1, 4):
        try:
            cam._log("Scanning for INSTAX (IOS) device ...")
            dev = await BleakScanner.find_device_by_filter(
                lambda d, a: d.address.upper() == ARG_ADDRESS,
                timeout=20,
            )
            if not dev:
                raise RuntimeError(f"Device {ARG_ADDRESS} not found")
            cam.address = dev.address
            cam._log(f"Found {dev.name!r} @ {dev.address}")

            cam._client = BleakClient(dev, timeout=30)
            await cam._client.connect()
            cam._log(f"Connected  MTU={cam._client.mtu_size}")

            if needs_pair:
                cam._log("Pairing ...")
                try:
                    await cam._client.pair()
                    cam._log("Paired")
                except Exception as pe:
                    cam._log(f"pair() exception: {pe} — waiting 3s then continuing")
                await asyncio.sleep(3.0)  # let WinRT complete security handshake
            else:
                # Mini Evo transfer-mode: give camera 2 s to settle before CCCD
                await asyncio.sleep(2.0)

            await cam._client.start_notify(NOTIFY_UUID, cam._notification_handler)
            cam._log("Subscribed to notify char — ready")
            break
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            try:
                await cam._client.disconnect()
            except Exception:
                pass
            if attempt == 3:
                raise
            await asyncio.sleep(3)

    print(f"Connected  MTU={cam._client.mtu_size}\n")

    async def xchg(op1, op2, payload=b"", timeout=5.0, label=""):
        dec = await cam._send_recv(op1, op2, payload, timeout=timeout)
        if not dec.get("error"):
            p = dec["payload"]
            print(f"  [{label or f'{op1:#04x},{op2:#04x}'}]  payload[{len(p)}B] = {p[:24].hex()}")
        else:
            print(f"  [{label or f'{op1:#04x},{op2:#04x}'}]  ERROR / timeout")
        return dec

    try:
        # ── Step 1: query slot count ──────────────────────────────────────
        print("── 0x84,0x09  LIVE_VIEW_SLOT_QUERY ──")
        meta = await xchg(0x84, 0x09, b"\x00", label="QUERY idx=0")
        mp = meta.get("payload", b"")

        if len(mp) >= 14:
            count = struct.unpack_from(">I", mp, 10)[0]
            if count == 0:
                # Wide Evo returns all-zeros — field does not encode count
                count = ARG_COUNT
                print(f"  → 14B all-zeros (count field empty) — "
                      f"trying {count} entries")
            else:
                print(f"  → entry count = {count}")
        else:
            # Mini Evo returns 1B \x80 — fall back to CLI arg
            count = ARG_COUNT
            print(f"  → short response ({len(mp)}B: {mp.hex()}) — "
                  f"trying {count} entries")

        # ── Steps 2–6: one pass per history entry ─────────────────────────
        for img_idx in range(count):
            print(f"\n{'─'*60}")
            print(f"── Entry {img_idx+1}/{count} (index={img_idx}) ──")

            await xchg(0x84, 0x0a, bytes([img_idx]) + bytes(4),
                       label=f"SUB_QUERY idx={img_idx}")
            await xchg(0x84, 0x0b, bytes([img_idx]),
                       label=f"ACK idx={img_idx}")
            await xchg(0x80, 0x15, bytes(17), label="PREPARE")

            print(f"\n── 0x82,0x00  LIVE_VIEW_START idx={img_idx} ──")
            start_dec = await xchg(0x82, 0x00, bytes([img_idx]),
                                   timeout=10.0, label=f"START idx={img_idx}")
            if start_dec.get("error"):
                print(f"  Camera rejected START — skipping entry {img_idx}")
                continue

            # ── Pull loop ──────────────────────────────────────────────────
            print(f"\n── 0x82,0x01  LIVE_VIEW_FRAME pulls ──")
            pull_pkt = create_packet(0x82, 0x01)
            jpeg_buf  = bytearray()
            t0 = time.time()

            for pull_num in range(2000):
                await cam._send(pull_pkt)

                # Camera can take >460 ms to prepare the first chunk.
                got_framed     = False
                transfer_ended = False
                chunk_bytes    = bytearray()

                try:
                    first_raw = await asyncio.wait_for(
                        cam._rx_queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    print(f"  pull {pull_num+1}: 5s timeout — camera stopped responding")
                    break

                if first_raw[:2] == b"\x61\x42":
                    dec = decode_response(first_raw)
                    if not dec.get("error"):
                        op1, op2 = dec["op"]
                        if op1 == 0x82 and op2 == 0x01:
                            p = dec["payload"]
                            got_framed = True
                            if len(p) >= 2:
                                chunk_idx = struct.unpack_from(">H", p, 0)[0]
                                chunk_bytes.extend(p[2:])
                            else:
                                chunk_idx = p[0] if p else 0
                            if pull_num < 5 or pull_num % 50 == 0:
                                print(f"  pull {pull_num+1}  chunk_idx={chunk_idx}  "
                                      f"framed_payload={len(p)}B  "
                                      f"total_so_far={len(jpeg_buf)}B  "
                                      f"t={time.time()-t0:.1f}s")
                        elif op1 == 0x82 and op2 == 0x02:
                            print(f"  Camera sent END at pull {pull_num+1}")
                            transfer_ended = True
                        else:
                            print(f"  Unexpected framed op=({op1:#04x},{op2:#04x}) "
                                  f"payload={dec['payload'].hex()!r}")
                    else:
                        chunk_bytes.extend(first_raw)
                else:
                    chunk_bytes.extend(first_raw)
                    if pull_num < 5:
                        print(f"  pull {pull_num+1}  first-raw [{len(first_raw)}B] "
                              f"{first_raw[:8].hex()}..")

                while not transfer_ended:
                    try:
                        raw = await asyncio.wait_for(
                            cam._rx_queue.get(), timeout=0.50)
                    except asyncio.TimeoutError:
                        break
                    if raw[:2] == b"\x61\x42":
                        dec = decode_response(raw)
                        if not dec.get("error"):
                            op1, op2 = dec["op"]
                            if op1 == 0x82 and op2 == 0x02:
                                transfer_ended = True
                                break
                            if op1 == 0x82 and op2 == 0x01:
                                cam._rx_queue.put_nowait(raw)
                                break
                        chunk_bytes.extend(raw)
                    else:
                        chunk_bytes.extend(raw)

                jpeg_buf.extend(chunk_bytes)
                if pull_num < 5 and chunk_bytes:
                    print(f"  pull {pull_num+1}  chunk_total={len(chunk_bytes)}B  "
                          f"first8={bytes(chunk_bytes[:8]).hex()}")

                eoi = jpeg_buf.find(b"\xff\xd9")
                if eoi != -1:
                    jpeg_buf = jpeg_buf[:eoi + 2]
                    print(f"\n  JPEG complete! {len(jpeg_buf)/1024:.1f} KB  "
                          f"after {pull_num+1} pulls")
                    break

                if transfer_ended or (not got_framed and not chunk_bytes):
                    print(f"\n  Transfer ended / no data at pull {pull_num+1}  "
                          f"total={len(jpeg_buf)}B")
                    break

            # ── End ────────────────────────────────────────────────────────
            await xchg(0x82, 0x02, bytes([img_idx]), timeout=5.0, label="END")

            # ── Save ───────────────────────────────────────────────────────
            # Strip IOS framing header before JPEG SOI (ff d8).
            # Payload prefix: [image_index 1B][chunk_num 1B][jpeg_size 3B BE]
            soi = jpeg_buf.find(b"\xff\xd8")
            if soi > 0:
                print(f"  Stripping {soi}B framing header before SOI")
                jpeg_buf = jpeg_buf[soi:]

            OUT_DIR.mkdir(parents=True, exist_ok=True)
            if jpeg_buf and jpeg_buf[:2] == b"\xff\xd8":
                idx = 0
                while True:
                    out = OUT_DIR / f"history-received-{idx}.jpg"
                    if not out.exists():
                        break
                    idx += 1
                out.write_bytes(jpeg_buf)
                print(f"\nSaved: {out}  ({len(jpeg_buf)/1024:.1f} KB)")
            elif jpeg_buf:
                out = OUT_DIR / f"history-received-{img_idx}-raw.bin"
                out.write_bytes(jpeg_buf)
                print(f"\nSaved raw: {out}  ({len(jpeg_buf)}B)  "
                      f"first16={bytes(jpeg_buf[:16]).hex()}")
            else:
                print(f"\nNo data for entry {img_idx}.")

            await asyncio.sleep(0.5)  # brief pause between entries

    finally:
        await cam.disconnect()
        print("Disconnected.")


asyncio.run(receive_history())

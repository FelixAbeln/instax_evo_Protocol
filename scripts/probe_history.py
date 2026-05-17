"""Probe 0x84,0x09 history query — uses InstaxCamera for proper connection setup."""

import asyncio
import struct
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from instax_lab.evo_protocol import InstaxCamera, create_packet, decode_response


async def probe():
    cam = InstaxCamera(verbose=True)
    await cam.connect()

    print(f"\nConnected to {cam.address!r}\n")

    async def exchange(op1, op2, payload=b"", label=""):
        label = label or f"({op1:#04x},{op2:#04x})"
        print(f"\n>>> {label}")
        dec = await cam._send_recv(op1, op2, payload, timeout=5.0)
        if not dec.get("error"):
            p = dec["payload"]
            print(f"    op={dec['op']}  payload[{len(p)}B]={p.hex()!r}")
        else:
            print(f"    error/timeout: {dec}")
        return dec

    try:
        # ---- Test 1: 0x84,0x09 without any prior init ----
        print("=== Test 1: 0x84,0x09 with NO prior init ===")
        dec = await exchange(0x84, 0x09, b"\x00", "HISTORY_ENTRY_QUERY index=0 (no init)")
        p = dec.get("payload", b"")
        if len(p) >= 14:
            count = struct.unpack_from(">I", p, 10)[0]
            print(f"    --> entry count = {count}")
        else:
            print(f"    --> short response ({len(p)}B) — camera may have no history")

        # ---- Test 2: 0x00,0x00 init then 0x84,0x09 ----
        print("\n=== Test 2: 0x00,0x00 init → 0x84,0x09 ===")
        await exchange(0x00, 0x00, b"", "HELLO/INIT")
        dec = await exchange(0x84, 0x09, b"\x00", "HISTORY_ENTRY_QUERY index=0 (after init)")
        p = dec.get("payload", b"")
        if len(p) >= 14:
            count = struct.unpack_from(">I", p, 10)[0]
            print(f"    --> entry count = {count}")
        else:
            print(f"    --> short response ({len(p)}B)")

        # ---- Test 3: 0x84,0x09 index=1 ----
        print("\n=== Test 3: 0x84,0x09 index=1 ===")
        dec = await exchange(0x84, 0x09, b"\x01", "HISTORY_ENTRY_QUERY index=1")
        p = dec.get("payload", b"")
        if len(p) >= 14:
            count = struct.unpack_from(">I", p, 10)[0]
            print(f"    --> entry count = {count}")
        else:
            print(f"    --> short response ({len(p)}B): {p.hex() if p else '(empty)'}")

        # ---- Test 4: jump straight to 0x82,0x00 DOWNLOAD_START ----
        print("\n=== Test 4: 0x82,0x00 HISTORY_DOWNLOAD_START index=0 ===")
        await exchange(0x82, 0x00, b"\x00", "HISTORY_DOWNLOAD_START index=0")

        # ---- Test 5: 0x82,0x01 single data pull ----
        print("\n=== Test 5: 0x82,0x01 single data pull ===")
        await exchange(0x82, 0x01, b"", "HISTORY_DOWNLOAD_DATA pull")

        # ---- End ----
        await exchange(0x82, 0x02, b"\x00", "HISTORY_DOWNLOAD_END")

    finally:
        await cam.disconnect()
        print("\nDisconnected.")


asyncio.run(probe())


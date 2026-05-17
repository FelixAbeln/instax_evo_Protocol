"""
Hold a BLE connection open and log every notification the camera sends.

Usage:
    python scripts/history_listen.py [--out captures/history-listen.jsonl]

Then use the camera's physical buttons to navigate history and trigger a share/send.
Press Ctrl+C when done.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from instax_lab.evo_protocol import (
    InstaxCamera, create_packet, decode_response
)


OUT_FILE = Path("captures/history-listen.jsonl")


async def listen(out_path: Path):
    cam = InstaxCamera(verbose=False)

    # Retry connect a few times — camera may need a moment after waking
    for attempt in range(1, 4):
        try:
            print(f"Connecting (attempt {attempt}/3) ...")
            await cam.connect(scan_timeout=30)
            break
        except Exception as e:
            print(f"  Failed: {e}")
            if attempt == 3:
                raise
            await asyncio.sleep(2)

    print(f"Connected to {cam.address!r}  MTU={cam._client.mtu_size}")
    print(f"Logging to {out_path}")
    print()
    print("Now use the camera buttons to navigate history and share an image.")
    print("Press Ctrl+C to stop.\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = out_path.open("a", encoding="utf-8")
    t0 = time.time()

    # Wrap the notification handler to also pretty-print and log
    original_handler = cam._notification_handler

    def rich_handler(sender, data: bytearray):
        raw = bytes(data)
        t = time.time() - t0
        dec = decode_response(raw)

        if dec.get("error"):
            label = f"RAW [{len(raw)}B]"
            detail = raw.hex()
        else:
            op1, op2 = dec["op"]
            p = dec["payload"]
            label = f"({op1:#04x},{op2:#04x}) payload[{len(p)}B]"
            detail = p.hex()

        print(f"  t+{t:7.3f}s  {label}  {detail}")

        record = {
            "t": time.time(),
            "rel_t": round(t, 4),
            "direction": "camera_to_phone",
            "raw": raw.hex(),
            "decoded": dec,
        }
        log_f.write(json.dumps(record) + "\n")
        log_f.flush()

        # Still feed the queue so _send_recv works if we need it
        cam._rx_queue.put_nowait(raw)

    # Replace the camera's notification handler with ours
    # (We must re-register the GATT notify callback)
    await cam._client.stop_notify(
        "70954784-2d83-473d-9e5f-81e1d02d5273"
    )
    await cam._client.start_notify(
        "70954784-2d83-473d-9e5f-81e1d02d5273",
        rich_handler,
    )

    try:
        while True:
            await asyncio.sleep(0.1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log_f.close()
        await cam.disconnect()
        print("\nDisconnected. Log saved to", out_path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    args = ap.parse_args()

    try:
        asyncio.run(listen(args.out))
    except KeyboardInterrupt:
        pass

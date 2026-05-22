

from .evo_protocol import InstaxCamera

import asyncio
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Optional

import typer
from bleak import BleakScanner, BleakClient
from rich import print
from rich.table import Table
from rich.console import Console

app = typer.Typer(help="instax BLE reverse engineering lab")
console = Console()


def _unique_output_path(out_dir: Path, preferred_name: str) -> Path:
    candidate = out_dir / preferred_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        candidate = out_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _extract_btsnoop_from_zip(zip_path: Path, out_dir: Path) -> list[Path]:
    extracted: list[Path] = []

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()

        for name in names:
            if "btsnoop" not in name.lower():
                continue

            in_zip_name = Path(name).name or "btsnoop.log"
            if not in_zip_name.lower().endswith(".log"):
                in_zip_name = f"{in_zip_name}.log"

            preferred = f"{zip_path.stem}__{in_zip_name}"
            out_path = _unique_output_path(out_dir, preferred)

            with archive.open(name) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)

            extracted.append(out_path)

        nested_zips = [name for name in names if name.lower().endswith(".zip")]
        for nested_name in nested_zips:
            nested_tmp = out_dir / Path(nested_name).name
            with archive.open(nested_name) as src, nested_tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst)

            try:
                extracted.extend(_extract_btsnoop_from_zip(nested_tmp, out_dir))
            except zipfile.BadZipFile:
                pass
            finally:
                try:
                    nested_tmp.unlink()
                except OSError:
                    pass

    return extracted


@app.command()
def evo_connect(
    address: Optional[str] = typer.Option(None, help="Device address (default: auto-scan)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output"),
):
    """Connect to an Instax camera, dump its GATT layout, and disconnect."""
    asyncio.run(_evo_connect(address, verbose))


async def _evo_connect(address: Optional[str], verbose: bool):
    cam = InstaxCamera(address=address, verbose=verbose)
    try:
        await cam.connect()
        console.print(f"[bold green]Connected[/bold green]  address={cam.address}  MTU={cam._client.mtu_size}")
        table = Table(title="GATT layout")
        table.add_column("Handle", style="green")
        table.add_column("UUID", style="yellow")
        table.add_column("Properties", style="cyan")
        for svc in cam._client.services:
            for char in svc.characteristics:
                table.add_row(str(char.handle), char.uuid, ", ".join(char.properties))
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await cam.disconnect()


@app.command()
def evo_status(
    address: Optional[str] = typer.Option(None, help="Device address (default: auto-scan)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Read battery level and film remaining from an Instax camera."""
    asyncio.run(_evo_status(address, verbose))


async def _evo_status(address: Optional[str], verbose: bool):
    _BATTERY_LABEL = {0: "CRITICAL", 1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "FULL"}
    cam = InstaxCamera(address=address, verbose=verbose)
    try:
        await cam.connect()
        console.print(f"Connected to [bold]{cam.address}[/bold]  MTU={cam._client.mtu_size}")
        status = await cam.get_status()
        console.print()
        console.print(f"  Model:        [cyan]{status['model']}[/cyan] ({status['manufacturer']})")
        console.print(f"  Serial:       {status['serial']}")
        console.print(f"  Film size:    {status['image_size'][0]}×{status['image_size'][1]} px")
        bat_label = _BATTERY_LABEL.get(status['battery_state'], f"state={status['battery_state']}")
        console.print(f"  Battery:      [green]{status['battery_pct']}%[/green] ({bat_label})")
        photos = status['photos_left']
        color = "green" if photos > 3 else "yellow" if photos > 0 else "red"
        console.print(f"  Photos left:  [{color}]{photos}[/{color}]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await cam.disconnect()


@app.command()
def evo_print(
    image: Path = typer.Argument(..., help="Path to the image to print"),
    address: Optional[str] = typer.Option(None, help="Device address (default: auto-scan)"),
    enable_print: bool = typer.Option(False, "--enable-print", help="Actually trigger the print (eject film)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Send an image to the Instax camera.

    By default only sends the image data (safe for testing).
    Pass --enable-print to physically eject and print the image.
    """
    asyncio.run(_evo_print(image, address, enable_print, verbose))


async def _evo_print(image: Path, address: Optional[str], enable_print: bool, verbose: bool):
    if not image.exists():
        console.print(f"[red]File not found: {image}[/red]")
        raise typer.Exit(code=1)

    cam = InstaxCamera(address=address, verbose=verbose)
    try:
        await cam.connect()
        console.print(f"Connected to [bold]{cam.address}[/bold]")

        console.print("Reading camera status ...")
        status = await cam.get_status()
        console.print(
            f"  {status['model']}  battery={status['battery_pct']}%  "
            f"photos_left={status['photos_left']}  "
            f"film={status['image_size'][0]}×{status['image_size'][1]}"
        )

        if status["photos_left"] == 0:
            console.print("[red]No photos left — load a new film pack[/red]")
            raise typer.Exit(code=1)

        action = "[bold red]PRINTING[/bold red]" if enable_print else "[yellow]sending data only (--enable-print not set)[/yellow]"
        console.print(f"\nSending image {image.name} ... {action}")

        await cam.print_image(image, enable_print=enable_print)

        if enable_print:
            console.print(f"[bold green]Print triggered![/bold green]  photos_left now={cam.photos_left}")
        else:
            console.print("[green]Image data sent successfully[/green] (no ejection)")

        # Append to print log
        log_path = Path("captures/analysis/logs/print-log.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "t": time.time(),
            "image": str(image.resolve()),
            "camera": cam.address,
            "model": status["model"],
            "transferred": True,
            "printed": enable_print,
            "photos_left_after": cam.photos_left,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        console.print(f"Logged to [dim]{log_path}[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await cam.disconnect()

@app.command()
def scan(timeout: float = typer.Option(10.0, help="Scan duration in seconds")):
    """Scan for nearby BLE devices."""
    asyncio.run(_scan(timeout))


async def _scan(timeout: float):
    console.print(f"[bold]Scanning for {timeout:.1f}s...[/bold]")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

    table = Table(title="BLE devices")
    table.add_column("Name")
    table.add_column("Address / ID")
    table.add_column("RSSI")
    table.add_column("Service UUIDs")
    table.add_column("Manufacturer data")

    for device, adv in devices.values():
        name = device.name or adv.local_name or "<unknown>"
        svc = ", ".join(adv.service_uuids or [])
        mfg = json.dumps({str(k): v.hex() for k, v in adv.manufacturer_data.items()})
        table.add_row(name, device.address, str(adv.rssi), svc, mfg)

    console.print(table)


@app.command()
def inspect(address: str):
    """Connect to a BLE device and dump its GATT database."""
    asyncio.run(_inspect(address))


async def _inspect(address: str):
    console.print(f"[bold]Connecting to {address}...[/bold]")
    async with BleakClient(address) as client:
        console.print(f"Connected: [green]{client.is_connected}[/green]")
        try:
            console.print(f"MTU: {client.mtu_size}")
        except Exception:
            pass

        for service in client.services:
            console.print()
            console.print(f"[bold cyan]Service {service.uuid}[/bold cyan]")
            console.print(f"  {service.description}")

            table = Table(show_header=True)
            table.add_column("Handle")
            table.add_column("Characteristic UUID")
            table.add_column("Properties")
            table.add_column("Description")

            for char in service.characteristics:
                table.add_row(
                    str(char.handle),
                    char.uuid,
                    ", ".join(char.properties),
                    char.description or "",
                )

            console.print(table)

            for char in service.characteristics:
                if char.descriptors:
                    console.print(f"  Descriptors for {char.uuid}:")
                    for desc in char.descriptors:
                        console.print(f"    {desc.uuid} handle={desc.handle}")


@app.command()
def notify(
    address: str,
    out: Path = typer.Option(Path("captures/notifications.jsonl"), help="Output JSONL file"),
    seconds: Optional[float] = typer.Option(None, help="Stop after N seconds; default runs until Ctrl+C"),
):
    """Subscribe to all notify/indicate characteristics and log incoming data."""
    asyncio.run(_notify(address, out, seconds))


async def _notify(address: str, out: Path, seconds: Optional[float]):
    out.parent.mkdir(parents=True, exist_ok=True)

    async with BleakClient(address) as client:
        console.print(f"Connected: [green]{client.is_connected}[/green]")

        notify_chars = []
        for service in client.services:
            for char in service.characteristics:
                if "notify" in char.properties or "indicate" in char.properties:
                    notify_chars.append(char.uuid)

        console.print(f"Notify/indicate characteristics: {notify_chars}")

        def make_handler(uuid: str):
            def handler(sender, data: bytearray):
                event = {
                    "t": time.time(),
                    "direction": "device_to_host",
                    "characteristic": uuid,
                    "sender": str(sender),
                    "data": bytes(data).hex(),
                }
                print(event)
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            return handler

        for uuid in notify_chars:
            try:
                await client.start_notify(uuid, make_handler(uuid))
                console.print(f"Subscribed: {uuid}")
            except Exception as e:
                console.print(f"[yellow]Could not subscribe {uuid}: {e}[/yellow]")

        if seconds is None:
            console.print("Listening. Press Ctrl+C to stop.")
            while True:
                await asyncio.sleep(1)
        else:
            await asyncio.sleep(seconds)


@app.command()
def replay(
    address: str,
    capture: Path,
    delay: float = typer.Option(0.03, help="Delay between writes in seconds"),
    response: bool = typer.Option(False, help="Use write-with-response"),
):
    """Replay JSONL records with direction=host_to_device or phone_to_camera."""
    asyncio.run(_replay(address, capture, delay, response))


async def _replay(address: str, capture: Path, delay: float, response: bool):
    records = []
    with capture.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    async with BleakClient(address) as client:
        console.print(f"Connected: [green]{client.is_connected}[/green]")

        for r in records:
            direction = r.get("direction")
            if direction not in ("host_to_device", "phone_to_camera", "write"):
                continue

            uuid = r["characteristic"]
            data = bytes.fromhex(r["data"])
            console.print(f"WRITE {uuid} {data.hex()}")
            await client.write_gatt_char(uuid, data, response=response)
            await asyncio.sleep(delay)


@app.command()
def make_test_images(outdir: Path = Path("test-images")):
    """Generate basic probe images for print payload comparison."""
    from PIL import Image, ImageDraw

    outdir.mkdir(parents=True, exist_ok=True)
    size = (800, 600)

    Image.new("RGB", size, "white").save(outdir / "white.jpg", quality=95)
    Image.new("RGB", size, "black").save(outdir / "black.jpg", quality=95)

    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 266, 599], fill="red")
    d.rectangle([267, 0, 533, 599], fill="green")
    d.rectangle([534, 0, 799, 599], fill="blue")
    img.save(outdir / "rgb-blocks.jpg", quality=95)

    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    block = 20
    for y in range(0, size[1], block):
        for x in range(0, size[0], block):
            if ((x // block) + (y // block)) % 2:
                d.rectangle([x, y, x + block - 1, y + block - 1], fill="black")
    img.save(outdir / "checkerboard.jpg", quality=95)

    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            v = int(255 * x / (size[0] - 1))
            px[x, y] = (v, v, v)
    img.save(outdir / "gradient-horizontal.jpg", quality=95)

    console.print(f"Created test images in {outdir}")


@app.command("extract-captures")
def extract_captures(
    source: Path = typer.Argument(
        Path("captures"),
        help="Zip file or directory containing Android bugreport zip files",
    ),
    out: Path = typer.Option(
        Path("captures/extracted"),
        help="Directory to store extracted btsnoop logs",
    ),
    keep_source: bool = typer.Option(
        False,
        "--keep-source",
        help="Keep original zip files after extraction",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show actions without extracting or deleting files",
    ),
):
    """Extract btsnoop logs from Android zip captures and optionally delete the source zips."""
    if source.is_file():
        zip_paths = [source] if source.suffix.lower() == ".zip" else []
    elif source.is_dir():
        zip_paths = sorted(p for p in source.rglob("*.zip") if p.is_file())
    else:
        raise typer.BadParameter(f"Source does not exist: {source}")

    if not zip_paths:
        console.print(f"[yellow]No zip files found in {source}[/yellow]")
        raise typer.Exit(code=1)

    out.mkdir(parents=True, exist_ok=True)
    extracted_total: list[Path] = []

    for zip_path in zip_paths:
        console.print(f"[bold]Processing {zip_path}[/bold]")
        if dry_run:
            console.print("  [cyan]Would extract btsnoop logs[/cyan]")
            if not keep_source:
                console.print("  [cyan]Would delete source zip after extraction[/cyan]")
            continue

        try:
            extracted = _extract_btsnoop_from_zip(zip_path, out)
        except zipfile.BadZipFile:
            console.print(f"  [yellow]Skipping invalid zip: {zip_path}[/yellow]")
            continue

        if not extracted:
            console.print("  [yellow]No btsnoop logs found[/yellow]")
            continue

        for path in extracted:
            console.print(f"  [green]Extracted:[/green] {path}")
        extracted_total.extend(extracted)

        if not keep_source:
            try:
                zip_path.unlink()
                console.print(f"  [green]Deleted source zip:[/green] {zip_path}")
            except OSError as exc:
                console.print(f"  [yellow]Could not delete {zip_path}: {exc}[/yellow]")

    if dry_run:
        console.print("\n[bold]Dry run complete.[/bold]")
        return

    if not extracted_total:
        console.print("\n[yellow]No btsnoop logs were extracted.[/yellow]")
        raise typer.Exit(code=2)

    console.print(f"\n[bold green]Done.[/bold green] Extracted {len(extracted_total)} file(s) to {out}")


@app.command("evo-history")
def evo_history(
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Device address (default: auto-scan)"),
    index: Optional[int] = typer.Option(None, "--index", "-i", help="Entry index to download (0-based); omit to list count"),
    out_dir: Path = typer.Option(Path("captures"), "--out-dir", "-o", help="Directory to save downloaded JPEG"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """List or download images from the camera's print history.

    Without --index: prints the total number of stored history entries.
    With --index N: downloads that entry and saves it as history-N.jpg.
    """
    asyncio.run(_evo_history(address, index, out_dir, verbose))


async def _evo_history(
    address: Optional[str],
    index: Optional[int],
    out_dir: Path,
    verbose: bool,
):
    cam = InstaxCamera(address=address, verbose=verbose)
    try:
        await cam.connect()
        console.print(f"Connected to [bold]{cam.address}[/bold]")

        # get_status() seeds image_size / camera strings used elsewhere
        await cam.get_status()

        if index is None:
            # List mode
            count = await cam.get_history_count()
            console.print(f"  History entries: [bold cyan]{count}[/bold cyan]")
            if count:
                console.print("  Use [bold]--index N[/bold] (0-based) to download an entry.")
        else:
            # Download mode
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = _unique_output_path(out_dir, f"history-{index}.jpg")

            console.print(f"  Downloading history entry {index} ...")
            jpeg_data = await cam.download_history_image(index)

            out_path.write_bytes(jpeg_data)
            console.print(
                f"  [bold green]Saved:[/bold green] {out_path} "
                f"({len(jpeg_data) / 1024:.1f} KB)"
            )

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await cam.disconnect()


@app.command("evo-favorites-dump")
def evo_favorites_dump(
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Device address (default: auto-scan)"),
    max_slot: int = typer.Option(10, "--max-slot", min=1, max=10, help="Highest slot index to read"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Optional output JSON file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Dump favorites slot selector-01 and selector-02 surfaces."""
    asyncio.run(_evo_favorites_dump(address, max_slot, out, verbose))


async def _evo_favorites_dump(
    address: Optional[str],
    max_slot: int,
    out: Optional[Path],
    verbose: bool,
):
    cam = InstaxCamera(address=address, verbose=verbose)
    try:
        await cam.connect()
        await cam.get_status()
        rows = await cam.favorites_dump_slots(max_slot=max_slot)

        table = Table(title=f"Favorites dump (slots 1..{max_slot})")
        table.add_column("Slot", style="green")
        table.add_column("Occ01", style="cyan")
        table.add_column("Occ02", style="cyan")
        table.add_column("Selector01", style="yellow")
        table.add_column("Selector02", style="yellow")
        for r in rows:
            table.add_row(
                str(r["slot"]),
                str(r["occupied_01"]),
                str(r["occupied_02"]),
                r["selector_01"],
                r["selector_02"],
            )
        console.print(table)

        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "address": cam.address,
                "model": cam.model,
                "serial": cam.serial,
                "max_slot": max_slot,
                "slots": rows,
            }
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            console.print(f"Saved [bold green]{out}[/bold green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await cam.disconnect()


@app.command("evo-favorites-write")
def evo_favorites_write(
    slot: int = typer.Option(..., "--slot", min=1, max=10, help="Favorites slot index"),
    profile_blob: str = typer.Option(..., "--profile-blob", help="8-byte profile blob hex (16 hex chars)"),
    title: str = typer.Option(..., "--title", help="3 ASCII chars"),
    state_blob: str = typer.Option("0000000000000000000000", "--state-blob", help="11-byte state blob hex (22 hex chars)"),
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Device address (default: auto-scan)"),
    verify_readback: bool = typer.Option(True, "--verify-readback/--no-verify-readback", help="Read slot back after write"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Write one favorites slot using the confirmed 0x85 + 0x80,17 flow."""
    asyncio.run(
        _evo_favorites_write(
            slot=slot,
            profile_blob=profile_blob,
            title=title,
            state_blob=state_blob,
            address=address,
            verify_readback=verify_readback,
            verbose=verbose,
        )
    )


async def _evo_favorites_write(
    slot: int,
    profile_blob: str,
    title: str,
    state_blob: str,
    address: Optional[str],
    verify_readback: bool,
    verbose: bool,
):
    cam = InstaxCamera(address=address, verbose=verbose)
    try:
        pb = bytes.fromhex(profile_blob)
        sb = bytes.fromhex(state_blob)
        await cam.connect()
        await cam.get_status()

        pre1 = await cam.favorites_read_slot(slot=slot, selector=1)
        pre2 = await cam.favorites_read_slot(slot=slot, selector=2)
        result = await cam.favorites_write_slot(
            slot=slot,
            profile_blob=pb,
            title=title,
            state_blob=sb,
        )

        console.print(f"[green]Wrote slot {slot}[/green]")
        console.print(f"  write_a={result['write_a']}")
        console.print(f"  write_b={result['write_b']}")
        console.print(f"  pre_sel1={pre1.hex()}")
        console.print(f"  pre_sel2={pre2.hex()}")

        if verify_readback:
            post1 = await cam.favorites_read_slot(slot=slot, selector=1)
            post2 = await cam.favorites_read_slot(slot=slot, selector=2)
            console.print(f"  post_sel1={post1.hex()}")
            console.print(f"  post_sel2={post2.hex()}")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await cam.disconnect()


@app.command("evo-favorites-write-default")
def evo_favorites_write_default(
    slot: int = typer.Option(..., "--slot", min=1, max=10, help="Favorites slot index"),
    title: str = typer.Option("DEF", "--title", help="3 ASCII chars"),
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Device address (default: auto-scan)"),
    verify_readback: bool = typer.Option(True, "--verify-readback/--no-verify-readback", help="Read slot back after write"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Write validated full-default favorites payload (Normal/Normal/AUTO/OFF-style state)."""
    asyncio.run(
        _evo_favorites_write(
            slot=slot,
            profile_blob="0000000032000000",
            title=title,
            state_blob="0000000000000000000000",
            address=address,
            verify_readback=verify_readback,
            verbose=verbose,
        )
    )


if __name__ == "__main__":
    app()

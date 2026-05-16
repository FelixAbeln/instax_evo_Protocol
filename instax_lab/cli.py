

from .evo_protocol import EvoProtocol

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
    verbose: bool = typer.Option(False, help="Enable verbose output"),
):
    asyncio.run(_evo_connect(address, verbose))


async def _evo_connect(address: Optional[str], verbose: bool):
    """Connect and discover Evo device"""
    proto = EvoProtocol(device_address=address, verbose=verbose)
    
    try:
        # Scan and connect
        if not await proto.scan_for_device():
            raise typer.Exit(code=1)
        
        if not await proto.connect():
            raise typer.Exit(code=1)
        
        # Discover services and characteristics
        if not await proto.discover_services():
            console.print("[yellow]Warning: Could not discover services[/yellow]")
        
        console.print("\n[bold green]✓ Connected and ready[/bold green]")
        console.print(f"[cyan]Address: {proto.device_address}[/cyan]")
        console.print(f"[cyan]MTU: {proto.client.mtu_size}[/cyan]")
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await proto.disconnect()



@app.command()
def evo_status(
    address: Optional[str] = typer.Option(None, help="Device address (default: auto-scan)"),
):
    """Read image count and battery level from Instax Evo device."""
    asyncio.run(_evo_status(address))


async def _evo_status(address: Optional[str]):
    proto = EvoProtocol(device_address=address, verbose=True)
    try:
        if not await proto.scan_for_device(timeout=5):
            raise typer.Exit(code=1)
        if not await proto.connect():
            raise typer.Exit(code=1)

        console.print("\n[bold cyan]Reading device status...[/bold cyan]")
        console.print("[dim]Discovering GATT services...[/dim]")
        await proto.discover_services()

        console.print("[dim]Polling device for status...[/dim]")

        # Status packets arrive during/after GATT discovery phase
        # Subscribe first, then discover services to trigger them
        status = await proto.read_device_status(timeout=8.0)

        battery = status.get("battery_pips", -1)
        img_count = status.get("images_queued", -1)
        all_pkts = status.get("all_packets", [])

        console.print(f"\n  Received {len(all_pkts)} notification packets")

        if battery >= 0:
            bar = "pip" if battery == 1 else "pips"
            label = "full" if battery == 3 else "partial" if battery > 0 else "low/empty"
            console.print(f"  [green]Battery:[/green] {battery} {bar} ({label})")
        else:
            console.print("  [yellow]Battery: not received[/yellow]")

        if img_count >= 0:
            console.print(f"  [green]Images queued:[/green] {img_count}")
        else:
            console.print("  [yellow]Image count: not received[/yellow]")

        if all_pkts:
            console.print("\n  [dim]All decoded packets:[/dim]")
            for p in all_pkts[:10]:
                console.print(f"    {p}")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await proto.disconnect()


@app.command()
def evo_query(
    address: Optional[str] = typer.Option(None, help="Device address (default: auto-scan)"),
    hex_data: Optional[str] = typer.Option(None, help="Hex-encoded command to send (e.g., A8010002FF)"),
    listen: float = typer.Option(2.0, help="Listen for responses for N seconds"),
):
    """Send a query command to Instax Evo and listen for response."""
    asyncio.run(_evo_query(address, hex_data, listen))


async def _evo_query(address: Optional[str], hex_data: Optional[str], listen: float):
    """Send command and listen for response"""
    proto = EvoProtocol(device_address=address, verbose=True)
    
    try:
        # Scan and connect
        if not await proto.scan_for_device(timeout=5):
            raise typer.Exit(code=1)
        
        if not await proto.connect():
            raise typer.Exit(code=1)
        
        # Setup notifications before sending command
        console.print("\n[bold cyan]Setting up notifications...[/bold cyan]")
        await proto.setup_notifications()
        
        # Send command if provided
        if hex_data:
            try:
                cmd = bytes.fromhex(hex_data)
                console.print(f"\n[bold cyan]Sending command:[/bold cyan]")
                await proto.send_command(cmd)
            except ValueError:
                console.print(f"[red]Invalid hex data: {hex_data}[/red]")
                raise typer.Exit(code=1)
        else:
            console.print(f"\n[bold cyan]No command specified. Listening for {listen}s...[/bold cyan]")
        
        # Listen for responses
        console.print(f"[cyan]Listening for responses ({listen}s)...[/cyan]\n")
        await asyncio.sleep(listen)
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        await proto.disconnect()

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


if __name__ == "__main__":
    app()

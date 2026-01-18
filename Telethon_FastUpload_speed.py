import argparse
import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from telethon import TelegramClient

MB = 1024 * 1024


def _env_proxy_url() -> Optional[str]:
    enabled = os.getenv("PROXY_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    if not host or not port:
        return None
    user = os.getenv("PROXY_USER")
    pwd = os.getenv("PROXY_PASS")
    auth = f"{user}:{pwd}@" if user else ("{}@".format(user) if pwd else "")
    return f"socks5://{auth}{host}:{port}"


def _parse_proxy(proxy_str: Optional[str]):
    if not proxy_str:
        return None
    parsed = urlparse(proxy_str)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise ValueError("代理 URL 缺少 scheme/host/port")
    scheme = parsed.scheme.lower()
    if scheme not in {"socks5", "socks5h", "socks4", "http", "https"}:
        raise ValueError(f"不支持的代理类型: {scheme}")
    username = parsed.username
    password = parsed.password
    return (scheme, parsed.hostname, parsed.port, True, username, password)


def _normalize_peer(value: str):
    s = value.strip()
    if s.lstrip("-").isdigit():
        return int(s)
    return value


def _iter_video_files(download_dir: Path, recursive: bool) -> list[Path]:
    exts = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".m4v", ".ts"}
    pattern = "**/*" if recursive else "*"
    files: list[Path] = []
    for p in download_dir.glob(pattern):
        if p.is_file() and p.suffix.lower() in exts:
            files.append(p)
    files.sort(key=lambda x: str(x).lower())
    return files


def _make_progress_printer(label: str, min_interval_sec: float = 0.5):
    start = time.monotonic()
    last = start
    last_bytes = 0

    def cb(current: int, total: int):
        nonlocal last, last_bytes
        now = time.monotonic()
        if now - last < min_interval_sec and current != total:
            return
        dt = now - last
        db = current - last_bytes
        inst = (db / MB) / dt if dt > 0 else 0.0
        avg = (current / MB) / (now - start) if now > start else 0.0
        percent = (current / total * 100) if total else 0.0
        sys.stdout.write(
            f"\r{label} {current / MB:8.2f}/{total / MB:8.2f} MB {percent:6.2f}% inst {inst:6.2f} MB/s avg {avg:6.2f} MB/s"
        )
        sys.stdout.flush()
        last = now
        last_bytes = current
        if current == total:
            sys.stdout.write("\n")
            sys.stdout.flush()

    return cb


def _prompt_yes_no(prompt: str, default_yes: bool = True) -> bool:
    suffix = " (Y/n): " if default_yes else " (y/N): "
    while True:
        s = input(prompt + suffix).strip().lower()
        if not s:
            return default_yes
        if s in {"y", "yes", "1", "true", "on"}:
            return True
        if s in {"n", "no", "0", "false", "off"}:
            return False
        print("请输入 y 或 n")


def _prompt_int(prompt: str, default: Optional[int] = None, min_value: Optional[int] = None) -> Optional[int]:
    hint = f"（默认 {default}）" if default is not None else "（留空表示默认）"
    while True:
        s = input(f"{prompt}{hint}: ").strip()
        if not s:
            return default
        if not s.lstrip("-").isdigit():
            print("请输入整数")
            continue
        v = int(s)
        if min_value is not None and v < min_value:
            print(f"请输入 >= {min_value} 的整数")
            continue
        return v


async def fasttelethon_upload_file_tuned(
    *,
    client: TelegramClient,
    file_path: Path,
    progress_callback,
    connections: Optional[int],
    part_size: int = 512 * 1024,
):
    """
    使用 FastTelethon 的多连接并行上传，但本地读取用 512KB 分片，避免 1KB 读取导致 Python 端变慢。
    """
    import FastTelethonhelper  # noqa: F401  # 确保其把 FastTelethon.py 加入 sys.path
    import FastTelethon  # type: ignore

    from telethon import helpers
    from telethon.tl.types import InputFile, InputFileBig

    file_size = file_path.stat().st_size
    file_id = helpers.generate_random_long()
    part_count = (file_size + part_size - 1) // part_size
    is_large = file_size > 10 * 1024 * 1024

    uploader = FastTelethon.ParallelTransferrer(client)  # type: ignore[attr-defined]
    await uploader._init_upload(  # type: ignore[attr-defined]
        connections=connections or uploader._get_connection_count(file_size),  # type: ignore[attr-defined]
        file_id=file_id,
        part_count=part_count,
        big=is_large,
    )

    uploaded = 0
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(part_size)
            if not chunk:
                break
            await uploader.upload(chunk)
            uploaded += len(chunk)
            if not is_large:
                md5.update(chunk)
            if progress_callback:
                try:
                    progress_callback(uploaded, file_size)
                except Exception:
                    pass

    await uploader.finish_upload()
    if is_large:
        return InputFileBig(file_id, part_count, file_path.name)
    return InputFile(file_id, part_count, file_path.name, md5.hexdigest())


async def main_async(args: argparse.Namespace) -> int:
    base_dir = Path(__file__).resolve().parent
    load_dotenv(dotenv_path=base_dir / ".env")

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session = os.getenv("TELEGRAM_SESSION", str(base_dir / "session.session"))
    phone = os.getenv("TELEGRAM_PHONE")
    target_raw = os.getenv("TELEGRAM_TARGET")

    if not api_id or not api_id.strip().isdigit():
        raise SystemExit("缺少 TELEGRAM_API_ID（需要整数）")
    if not api_hash:
        raise SystemExit("缺少 TELEGRAM_API_HASH")
    if not target_raw:
        raise SystemExit("缺少 TELEGRAM_TARGET")

    download_dir_raw = os.getenv("TELEGRAM_DOWNLOAD_DIR") or str(base_dir / "downloads")
    download_dir = Path(download_dir_raw).expanduser().resolve()
    if not download_dir.exists():
        raise SystemExit(f"downloads 目录不存在: {download_dir}")

    proxy = None
    if not args.no_proxy:
        proxy_str = os.getenv("TELEGRAM_PROXY") or _env_proxy_url()
        proxy = _parse_proxy(proxy_str)

    client = TelegramClient(
        session,
        int(api_id),
        api_hash,
        use_ipv6=False,
        proxy=proxy,
    )

    await client.start(phone=phone)
    try:
        target = await client.get_entity(_normalize_peer(target_raw))
        files = _iter_video_files(download_dir, recursive=args.recursive)
        if args.limit:
            files = files[: args.limit]
        if not files:
            print(f"未在 {download_dir} 找到视频文件")
            return 0

        print(f"目标: {target_raw}")
        print(f"目录: {download_dir}")
        print(f"文件数: {len(files)}")

        total_bytes = 0
        total_sec = 0.0

        for idx, path in enumerate(files, start=1):
            size = path.stat().st_size
            label = path.name[-60:]
            progress_cb = _make_progress_printer(label)

            print(f"\n[{idx}/{len(files)}] {path.name} ({size / MB:.2f} MB)")
            t0 = time.monotonic()
            tgfile = await fasttelethon_upload_file_tuned(
                client=client,
                file_path=path,
                progress_callback=progress_cb,
                connections=args.connections,
            )
            await client.send_file(target, tgfile, supports_streaming=True)
            t1 = time.monotonic()

            sec = t1 - t0
            avg = (size / MB) / sec if sec > 0 else 0.0
            print(f"完成: {path.name} 用时 {sec:.2f}s 平均 {avg:.2f} MB/s")

            total_bytes += size
            total_sec += sec

        total_avg = (total_bytes / MB) / total_sec if total_sec > 0 else 0.0
        print(f"\n总计: {total_bytes / MB:.2f} MB / {total_sec:.2f}s = {total_avg:.2f} MB/s")
        return 0
    finally:
        await client.disconnect()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FastTelethon 多连接上传测速示例（读取 .env + 现有 session）")
    p.add_argument("--limit", type=int, default=None, help="最多上传多少个文件（默认全部）")
    p.add_argument("--recursive", action="store_true", help="递归扫描 downloads 子目录")
    p.add_argument("--no-proxy", action="store_true", help="忽略 .env 的代理配置")
    p.add_argument("--connections", type=int, default=None, help="强制连接数（默认按文件大小自动）")
    return p.parse_args()


def main() -> None:
    if len(sys.argv) == 1:
        print("交互模式：无需参数，按提示输入即可。\n")
        args = argparse.Namespace()
        args.limit = _prompt_int("最多上传多少个文件", default=None, min_value=1)
        args.recursive = _prompt_yes_no("递归扫描 downloads 子目录？", default_yes=False)
        args.no_proxy = _prompt_yes_no("忽略代理（--no-proxy）？", default_yes=True)
        args.connections = _prompt_int("连接数（建议 8~20）", default=16, min_value=1)
    else:
        args = parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()

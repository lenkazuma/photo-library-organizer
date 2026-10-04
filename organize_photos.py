"""Preview-first month-folder organization. No personal paths or data included."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

MONTH_RE = re.compile(r"^(\d{4}(?:0[1-9]|1[0-2]))(?:[_-].*)?$")
CATEGORIES = {"video", "screenshot", "text"}
VIDEO_EXTS = {".3g2", ".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".wmv"}
MEDIA_EXTS = {".arw", ".avif", ".bmp", ".dng", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"} | VIDEO_EXTS
SCREENSHOT_RE = re.compile(r"screenshot|screen shot|screencap|screen_capture|截屏|截图|snip", re.I)
SCREENSHOT_DIMS = {(640,1136), (750,1334), (828,1792), (1080,1920), (1080,2340), (1080,2400), (1125,2436), (1170,2532), (1179,2556), (1242,2688), (1284,2778), (1290,2796), (1320,2868), (1440,2560), (1440,2960), (1440,3040), (1440,3200), (1536,2048), (1620,2160), (1668,2224), (1668,2388), (2048,2732)}


def linked(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def hidden(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & 2)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def is_screenshot(path: Path) -> bool:
    if SCREENSHOT_RE.search(path.name):
        return True
    if path.suffix.lower() == ".png":
        try:
            with path.open("rb") as stream:
                h = stream.read(24)
            if len(h) == 24 and h[:8] == b"\x89PNG\r\n\x1a\n":
                w, height = int.from_bytes(h[16:20], "big"), int.from_bytes(h[20:24], "big")
                return (w, height) in SCREENSHOT_DIMS or (height, w) in SCREENSHOT_DIMS
        except OSError:
            pass
    return False


class Organizer:
    def __init__(self, root: Path, *, folders=("Selfie",), defer_screenshots=False):
        raw = Path(os.path.abspath(root.expanduser()))
        if linked(raw) or any(linked(p) for p in raw.parents):
            raise ValueError("Library root must not be a symlink or directory junction")
        if not raw.is_dir() or raw == Path(raw.anchor):
            raise ValueError("--root must be an existing photo-library directory, not a drive root")
        self.root = raw.resolve()
        self.folders = set(folders)
        if any(not n or n in {".", ".."} or "/" in n or "\\" in n or n.startswith(("_", ".")) for n in self.folders):
            raise ValueError("--folder requires a simple top-level folder name")
        self.defer_screenshots = defer_screenshots

    def safe(self, path: Path) -> Path:
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.root) or not path.resolve().is_relative_to(self.root):
            raise ValueError(f"Path escapes the library: {path}")
        current = path
        while current != self.root:
            if linked(current):
                raise ValueError(f"Linked path is not allowed: {current}")
            current = current.parent
        return path

    def scoped(self, path: Path) -> bool:
        parts = path.relative_to(self.root).parts
        return bool(parts and (MONTH_RE.fullmatch(parts[0]) or parts[0] in self.folders)
                    and not any(n.startswith(("_", ".")) or n == "[FORMAT]" for n in parts))

    def walk(self):
        for cur, dirs, files in os.walk(self.root, followlinks=False):
            base = Path(cur)
            dirs[:] = sorted(d for d in dirs if self.scoped(base / d) and not linked(base / d) and not hidden(base / d))
            for name in sorted(files):
                p = base / name
                if self.scoped(p) and not linked(p) and not hidden(p) and p.is_file():
                    yield p

    def month_bases(self):
        bases = set()
        for cur, dirs, _ in os.walk(self.root, followlinks=False):
            base = Path(cur)
            dirs[:] = sorted(d for d in dirs if self.scoped(base / d) and not linked(base / d) and not hidden(base / d))
            for name in dirs:
                match = MONTH_RE.fullmatch(name)
                if match:
                    bases.add(self.root / match.group(1))
                elif base == self.root and name in self.folders:
                    bases.add(self.root / name)
        return bases

    def target(self, path: Path) -> Path:
        parts = path.relative_to(self.root).parts
        month = None
        for part in parts[:-1]:
            match = MONTH_RE.fullmatch(part)
            if match:
                month = match.group(1)
        base = self.root / (month or parts[0])
        for part in reversed(parts[:-1]):
            if part.lower() in CATEGORIES | {"screeshot"}:
                return base / ("screenshot" if part.lower() == "screeshot" else part.lower()) / path.name
        if path.suffix.lower() in VIDEO_EXTS:
            return base / "video" / path.name
        if not self.defer_screenshots and is_screenshot(path):
            return base / "screenshot" / path.name
        return base / path.name

    def plan(self, *, deduplicate=False, remove_aae=False) -> list[dict]:
        files = [p for p in self.walk() if p.suffix.lower() in MEDIA_EXTS | {".aae"}]
        media = [p for p in files if p.suffix.lower() in MEDIA_EXTS]
        duplicates = {}
        if deduplicate:
            by_size = defaultdict(list)
            for p in media:
                by_size[p.stat().st_size].append(p)
            for candidates in by_size.values():
                if len(candidates) < 2:
                    continue
                by_hash = defaultdict(list)
                for p in candidates:
                    by_hash[sha256(p)].append(p)
                for group in by_hash.values():
                    group.sort(key=lambda p: (p.parent.name.lower() not in CATEGORIES, len(p.parts), str(p).lower()))
                    for p in group[1:]:
                        duplicates[p] = group[0]
        operations = []
        final_paths = {}
        reserved = set()
        bases = self.month_bases()
        for p in media:
            if p in duplicates:
                continue
            dest = self.target(p)
            base = dest.parent.parent if dest.parent.name in CATEGORIES else dest.parent
            bases.add(base)
            candidate = dest
            counter = 1
            while candidate in reserved or (candidate.exists() and candidate != p):
                candidate = dest.with_name(f"{dest.stem}_{counter}{dest.suffix}")
                counter += 1
            self.safe(candidate)
            reserved.add(candidate)
            final_paths[p] = candidate
            if candidate != p:
                operations.append(self.operation("move", p, candidate))
        for duplicate, keep in duplicates.items():
            operations.append(self.operation("delete_duplicate", duplicate, final_paths[keep]))
        if remove_aae:
            for p in files:
                if p.suffix.lower() == ".aae":
                    operations.append(self.operation("delete_aae", p))
        create_dirs = []
        for base in sorted(bases):
            for dest in (base, *(base / name for name in sorted(CATEGORIES))):
                self.safe(dest)
                if not dest.exists():
                    create_dirs.append({"action": "mkdir", "destination": str(dest.relative_to(self.root))})
        return create_dirs + operations

    def operation(self, action, source, dest=None):
        self.safe(source)
        size, mtime = fingerprint(source)
        return {"action": action, "source": str(source.relative_to(self.root)),
                "destination": str(dest.relative_to(self.root)) if dest else "",
                "size": size, "mtime_ns": mtime, "sha256": sha256(source)}

    def execute(self, plan: list[dict], log_path: Path) -> None:
        log_path = log_path.expanduser().absolute()
        if linked(log_path) or any(linked(p) for p in log_path.parents):
            raise ValueError("Audit path must not use links")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects prior audits and prevents accidental media overwrite.
        with log_path.open("x", encoding="utf-8") as journal:
            def log(op, status, error=None):
                journal.write(json.dumps({**op, "status": status, "error": error,
                    "time": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
            for op in plan:
                log(op, "planned")
                try:
                    action = op["action"]
                    dest = self.safe(self.root / op["destination"]) if op.get("destination") else None
                    if action == "mkdir":
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        src = self.safe(self.root / op["source"])
                        if fingerprint(src) != (op["size"], op["mtime_ns"]) or sha256(src) != op["sha256"]:
                            raise RuntimeError(f"Source changed since preview: {src}")
                        if action == "move":
                            self.copy_verified(src, dest, op["sha256"])
                        elif action == "delete_duplicate":
                            if src == dest or not dest.is_file() or sha256(dest) != op["sha256"]:
                                raise RuntimeError("Retained duplicate is missing or has changed")
                            src.unlink()
                        elif action == "delete_aae":
                            if src.suffix.lower() != ".aae":
                                raise ValueError("Only AAE sidecars may be removed")
                            src.unlink()
                        else:
                            raise ValueError(f"Unknown action: {action}")
                    log(op, "verified")
                except Exception as exc:
                    log(op, "failed", str(exc))
                    raise

    def copy_verified(self, source: Path, destination: Path, digest: str):
        self.safe(source)
        self.safe(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = fingerprint(source)
        # Exclusive create never overwrites an existing photo. On failure retain both files.
        with destination.open("xb") as target, source.open("rb") as stream:
            shutil.copyfileobj(stream, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if sha256(destination) != digest or fingerprint(source) != before or sha256(source) != digest:
            raise RuntimeError("Copy verification failed; source and destination retained for inspection")
        shutil.copystat(source, destination)
        self.safe(source).unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preview-first photo organizer / 照片整理（默认只预览）")
    parser.add_argument("--root", required=True, type=Path, help="Photo library root / 照片库根目录")
    parser.add_argument("--apply", action="store_true", help="Apply this run's plan / 执行本次计划")
    parser.add_argument("--deduplicate", action="store_true", help="Opt in to SHA-256 duplicate removal / 启用完全重复文件删除")
    parser.add_argument("--remove-aae", action="store_true", help="Opt in to Apple edit sidecar deletion / 启用 AAE 编辑记录删除")
    parser.add_argument("--defer-screenshots", action="store_true", help="Review text/chat images before screenshot classification / 先复核文字聊天图片")
    parser.add_argument("--folder", action="append", default=["Selfie"], help="Additional top-level album / 额外顶层相册")
    parser.add_argument("--log", type=Path, help="New audit JSONL path; default .photo-organizer/logs/ under root")
    parser.add_argument("--lang", choices=["en", "zh"], default="en")
    args = parser.parse_args(argv)
    try:
        organizer = Organizer(args.root, folders=args.folder, defer_screenshots=args.defer_screenshots)
        plan = organizer.plan(deduplicate=args.deduplicate, remove_aae=args.remove_aae)
        for op in plan:
            print(json.dumps(op, ensure_ascii=False))
        if args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            log_path = args.log or organizer.root / ".photo-organizer" / "logs" / f"organize_{stamp}.jsonl"
            organizer.execute(plan, log_path)
            print(("日志：" if args.lang == "zh" else "Audit: ") + str(log_path))
        elif args.log:
            args.log.parent.mkdir(parents=True, exist_ok=True)
            with args.log.open("x", encoding="utf-8") as stream:
                for op in plan:
                    stream.write(json.dumps({**op, "status": "preview"}, ensure_ascii=False) + "\n")
        summary = dict(Counter(op["action"] for op in plan))
        label = ("已执行" if args.apply else "仅预览；未修改照片") if args.lang == "zh" else ("APPLIED" if args.apply else "PREVIEW; photos unchanged")
        print(f"{label}: {json.dumps(summary, ensure_ascii=False)}")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Error / 错误: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

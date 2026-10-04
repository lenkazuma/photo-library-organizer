"""Local OCR shortlist -> visual review -> explicitly authorized, verified moves."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import uuid

import ocr_detector

FORMAT = 'photo-library-organizer.text-review.v1'
MONTH = re.compile(r'\d{4}(0[1-9]|1[0-2])')


def is_link(path):
    """Detect symbolic links and Windows junctions, including broken links."""
    if path.is_symlink():
        return True
    try:
        return getattr(path.lstat(), 'st_reparse_tag', 0) in {0xA0000003, 0xA000000C}
    except FileNotFoundError:
        return False


def reject_links(path):
    for part in (path, *path.parents):
        if is_link(part):
            raise ValueError(f'Symbolic links and junctions are not allowed: {part}')


def fingerprint(path):
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def write_json(path, value):
    reject_links(path)
    handle, temporary = tempfile.mkstemp(prefix='.write-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    reject_links(path)
    return json.loads(path.read_text(encoding='utf-8'))


def envelope(root, **data):
    return {'format': FORMAT, 'root': str(root), **data}


def read_bound(path, root, default=None):
    if not path.exists():
        if default is not None:
            return default
        raise ValueError(f'Missing state file; run scan first: {path}')
    result = read_json(path)
    if not isinstance(result, dict) or result.get('format') != FORMAT or result.get('root') != str(root):
        raise ValueError(f'State belongs to a different root or format: {path}')
    return result


def prepare(root, state_dir=None):
    root = Path(os.path.abspath(root))
    reject_links(root)
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Root must be an existing directory')
    state_base = Path(os.path.abspath(state_dir)) if state_dir else root / '.photo-organizer'
    state = state_base / 'text-review'
    reject_links(state)
    state.mkdir(parents=True, exist_ok=True)
    state = state.resolve(strict=True)
    owner = state / 'owner.json'
    if owner.exists():
        read_bound(owner, root)
    elif any(state.iterdir()):
        raise ValueError('Refusing an existing unrecognized text-review state directory')
    else:
        write_json(owner, envelope(root))
    return root, state


def source_path(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('Source must use a relative YYYYMM/filename path')
    parts = PurePosixPath(relative).parts
    if len(parts) != 2 or not MONTH.fullmatch(parts[0]) or parts[1] in {'.', '..'}:
        raise ValueError(f'Source outside allowed month root: {relative}')
    path = root.joinpath(*parts)
    reject_links(path)
    if path.suffix.lower() not in ocr_detector.IMAGE_EXTENSIONS or not path.is_file():
        raise ValueError(f'Not an in-scope image file: {relative}')
    if path.resolve().parent.parent != root:
        raise ValueError(f'Source escaped root: {relative}')
    return path


def iter_images(root):
    for month in sorted(root.iterdir()):
        if is_link(month) or not MONTH.fullmatch(month.name) or not month.is_dir():
            continue
        for path in sorted(month.iterdir()):
            if not is_link(path) and path.is_file() and path.suffix.lower() in ocr_detector.IMAGE_EXTENSIONS:
                yield path


def scan(root, state_dir=None, reconsider_all=False, include_all=False, detector=None):
    root, state = prepare(root, state_dir)
    cached = read_bound(state / 'cache.json', root, {'records': {}})['records']
    reviewed = read_bound(state / 'reviewed.json', root, {'records': {}})['records']
    detector = detector or ocr_detector.scan_image
    records, candidates, errors = {}, [], []
    detected = reused = 0
    for path in iter_images(root):
        relative = path.relative_to(root).as_posix()
        before = fingerprint(path)
        previous = cached.get(relative, {})
        if previous.get('fingerprint') == before and 'error' not in previous:
            record = dict(previous)
            reused += 1
        else:
            try:
                scores = detector(path)
                if fingerprint(path) != before or is_link(path):
                    raise ValueError('Image changed during detection; scan again')
                record = {'fingerprint': before, 'scores': scores}
            except Exception as exc:
                record = {'fingerprint': before, 'error': f'{type(exc).__name__}: {exc}'}
            detected += 1
        records[relative] = record
        if 'error' in record:
            errors.append({'path': relative, **record})
        elif (reconsider_all or reviewed.get(relative) != before) and (include_all or ocr_detector.is_candidate(record['scores'])):
            candidates.append({'id': str(len(candidates)), 'path': relative, **record})
    scan_id = uuid.uuid4().hex
    manifest = envelope(root, scan_id=scan_id, created_at=datetime.now(timezone.utc).isoformat(),
                        candidates=candidates, errors=errors, include_all=include_all)
    write_json(state / 'cache.json', envelope(root, records=records))
    write_json(state / 'current.json', manifest)
    write_json(state / 'decisions.template.json', envelope(root, scan_id=scan_id,
               decisions={row['id']: None for row in candidates}))
    write_json(state / 'errors.json', envelope(root, scan_id=scan_id, errors=errors))
    return {'images': len(records), 'detected': detected, 'cached': reused,
            'candidates': len(candidates), 'errors_retained': len(errors),
            'state_directory': str(state), 'scan_id': scan_id}


def preview(root, state_dir=None):
    root, state = prepare(root, state_dir)
    manifest = read_bound(state / 'current.json', root)
    cards = []
    for record in manifest['candidates']:
        path = source_path(root, record['path'])
        if fingerprint(path) != record['fingerprint']:
            raise ValueError(f'Image changed; run scan again: {record["path"]}')
        cards.append('<article><h2>ID ' + html.escape(record['id']) + '</h2><p>'
                     + html.escape(record['path']) + '</p><a href="'
                     + html.escape(path.as_uri(), quote=True) + '"><img loading="lazy" src="'
                     + html.escape(path.as_uri(), quote=True) + '" alt="Candidate photograph"></a></article>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Local text image review / 本地文字图片复核</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{font:16px system-ui,sans-serif;margin:2rem;color:#17212b;background:#f4f6f8}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}
article{background:white;padding:1rem;border-radius:8px;overflow-wrap:anywhere}img{width:100%;height:360px;object-fit:contain}
</style><h1>Text image review / 文字图片复核</h1><p>Review every candidate. Click a photo to inspect it.
Set each ID to text or keep in a copy of decisions.template.json. Preview makes no decisions.<br>
逐一查看候选图片；点击图片检查细节。在 decisions.template.json 的副本中为每个 ID 填写 text 或 keep。
预览不会自动做出判断。HEIC/HEIF previews may require a desktop image viewer / 部分格式需用本机图片查看器打开。</p>'''
    page += '<p>Scan / 扫描 ID: ' + html.escape(manifest['scan_id']) + '</p><main>' + ''.join(cards) + '</main></html>'
    output = state / 'review.html'
    reject_links(output)
    output.write_text(page, encoding='utf-8')
    return {'candidates': len(cards), 'preview': str(output)}


def destination_for(source, reserved):
    folder = source.parent / 'text'
    reject_links(folder)
    if folder.exists() and not folder.is_dir():
        raise ValueError(f'Destination category is not a directory: {folder}')
    destination = folder / source.name
    number = 1
    while os.path.lexists(destination) or destination in reserved:
        destination = folder / f'{source.stem}_text_{number}{source.suffix}'
        number += 1
    return destination


def apply_decisions(root, decisions_file, state_dir=None, apply=False):
    root, state = prepare(root, state_dir)
    manifest = read_bound(state / 'current.json', root)
    chosen = read_bound(Path(decisions_file), root)
    if chosen.get('scan_id') != manifest['scan_id']:
        raise ValueError('Decisions are from a different scan; review the current candidates')
    decisions = chosen.get('decisions')
    candidates = manifest['candidates']
    ids = [row['id'] for row in candidates]
    if (len(set(ids)) != len(ids) or not isinstance(decisions, dict)
            or set(decisions) != set(ids) or any(not isinstance(value, str) or value not in {'text', 'keep'} for value in decisions.values())):
        raise ValueError('Every current candidate needs exactly one explicit text or keep decision')
    planned, reserved, paths = [], set(), set()
    # Validate the complete batch before changing any source file.
    for record in candidates:
        source = source_path(root, record['path'])
        if source in paths:
            raise ValueError('Duplicate source in candidate manifest')
        paths.add(source)
        if fingerprint(source) != record['fingerprint']:
            raise ValueError(f'Source changed since scan: {record["path"]}')
        destination = destination_for(source, reserved) if decisions[record['id']] == 'text' else None
        if destination:
            reserved.add(destination)
        planned.append((record, source, destination))
    journal = state / ('apply_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
                       + '_' + uuid.uuid4().hex[:12] + '.jsonl')
    moved = 0
    reviewed = read_bound(state / 'reviewed.json', root, {'records': {}})['records']
    with journal.open('x', encoding='utf-8') as log:
        def record_event(event):
            log.write(json.dumps(event, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())
        for record, source, destination in planned:
            event = {'scan_id': manifest['scan_id'], 'source': record['path'],
                     'decision': decisions[record['id']], 'dry_run': not apply}
            if destination:
                reject_links(source)
                reject_links(destination)
                if fingerprint(source) != record['fingerprint']:
                    raise ValueError(f'Source changed before move: {record["path"]}')
                digest = sha256(source)
                event.update(destination=destination.relative_to(root).as_posix(), sha256=digest)
                record_event({**event, 'status': 'planned'})
                if apply:
                    destination.parent.mkdir(exist_ok=True)
                    reject_links(destination)
                    try:
                        # Exclusive creation prevents overwriting even if a name appears after preview.
                        with source.open('rb') as incoming, destination.open('xb') as outgoing:
                            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                            outgoing.flush()
                            os.fsync(outgoing.fileno())
                        shutil.copystat(source, destination, follow_symlinks=False)
                        reject_links(source)
                        reject_links(destination)
                        if (fingerprint(source) != record['fingerprint']
                                or sha256(source) != digest or sha256(destination) != digest):
                            raise RuntimeError('Verification failed; source retained, inspect journal and destination')
                        record_event({**event, 'status': 'copy_verified'})
                        source.unlink()
                        if source.exists() or sha256(destination) != digest:
                            raise RuntimeError('Final move verification failed; inspect journal')
                    except Exception as exc:
                        record_event({**event, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}'})
                        raise
                    record_event({**event, 'status': 'verified'})
                    moved += 1
            else:
                record_event({**event, 'status': 'keep' if apply else 'planned'})
            if apply:
                reviewed[record['path']] = record['fingerprint']
                write_json(state / 'reviewed.json', envelope(root, records=reviewed))
    return {'dry_run': not apply, 'planned_moves': sum(dest is not None for _, _, dest in planned),
            'moved_and_verified': moved, 'kept': sum(dest is None for _, _, dest in planned), 'journal': str(journal)}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['scan', 'preview', 'apply'])
    parser.add_argument('--root', required=True, type=Path, help='Existing photo-library root; always explicit')
    parser.add_argument('--state-dir', type=Path, help='Local cache/audit base; default ROOT/.photo-organizer')
    parser.add_argument('--decisions', type=Path, help='Reviewed JSON copied from decisions.template.json')
    parser.add_argument('--apply', action='store_true', help='Actually move images; apply mode otherwise only previews')
    parser.add_argument('--reconsider-all', action='store_true', help='Ignore past human decisions; reuse unchanged detection scores')
    parser.add_argument('--include-all', action='store_true', help='Include all successfully decoded images for review, even below the heuristic threshold')
    args = parser.parse_args(argv)
    if args.apply and args.mode != 'apply':
        parser.error('--apply is only allowed with apply mode')
    if args.mode != 'scan' and (args.reconsider_all or args.include_all):
        parser.error('--reconsider-all and --include-all are scan options')
    if args.mode == 'apply' and not args.decisions:
        parser.error('apply mode requires --decisions')
    try:
        if args.mode == 'scan':
            result = scan(args.root, args.state_dir, args.reconsider_all, args.include_all)
        elif args.mode == 'preview':
            result = preview(args.root, args.state_dir)
        else:
            result = apply_decisions(args.root, args.decisions, args.state_dir, args.apply)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(1, f'Error: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

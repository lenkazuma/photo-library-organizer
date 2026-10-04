# Photo Library Organizer

[English](README.md) | [简体中文](README.zh-CN.md)

A personal archive of local photo-management tools: preview-first month-folder
organization, human-reviewed text/chat-image sorting, and offline metadata search.
This repository contains code and synthetic examples only.

## What is included

- Merge `YYYYMM`, `YYYYMM_a`, and `YYYYMM-*` folder layouts. The deepest month in a
  nested path wins. This organizer uses folder names, not EXIF dates, to choose months.
- Separate videos and screenshot candidates; preserve existing `text`, `screenshot`
  and `video` categories. Screenshot names and PNG dimensions are heuristics.
- Optional byte-identical duplicate removal using SHA-256; optional AAE removal.
  Both require their own flags, in addition to `--apply`.
- OCR proposes text/chat-image candidates; a person reviews and decides before moving.
- Read capture time, GPS and camera metadata; search by time, location radius and
  other fields; export location observations and possible country-change intervals.
  See [metadata search](docs/metadata.md).

## Install

Python 3.12+ is required. The folder organizer uses only the standard library.

```sh
git clone https://github.com/lenkazuma/photo-library-organizer.git
cd photo-library-organizer
python -m venv .venv
# Activate your environment, then:
python -m pip install -e ".[metadata]"
# Optional HEIC/HEIF support:
python -m pip install -e ".[heif]"
# Optional OCR candidate detection and preview sheets:
python -m pip install -e ".[ocr]"
```

Without installation, run `python organize_photos.py --help`. The other entry
points are `python photo_metadata.py` and `python text_review.py`.

## Organize a library

Use a backed-up copy for the first run. Always pass the library path explicitly.

```sh
# Preview only; does not create folders, write a log or alter media:
photo-organize --root "/path/to/photos" --defer-screenshots

# Apply moves after inspecting the preview:
photo-organize --root "/path/to/photos" --defer-screenshots --apply

# Complete text/chat review before classifying new screenshots:
# Follow docs/text-review.md, then preview and apply the remaining screenshot moves.
photo-organize --root "/path/to/photos"
photo-organize --root "/path/to/photos" --apply

# Optional cleanup: preview first, then repeat with --apply if appropriate:
photo-organize --root "/path/to/photos" --deduplicate --remove-aae
```

`--root "E:\Photos"` works on Windows. Add `--lang zh` for Chinese summary text.
`--folder Holiday` includes an additional top-level album; `Selfie` is included by
default. Other top-level albums and loose root files are left alone. Existing empty
source folders are retained. Unknown files are not deleted.

On apply, the tool writes a new JSONL journal under `.photo-organizer/logs/`.
Use `--log /private/path/new-audit.jsonl` to choose another new log file. With no
`--apply`, `--log` explicitly opts into saving the preview; otherwise preview writes
nothing. Plans are regenerated each run, and sources are checked again before changes.

## Search photos to help reconstruct travel

See the [English guide](docs/metadata.md) or [中文指南](docs/metadata.zh-CN.md) for
working index/search/timeline commands. Locations are resolved from your own local
region definitions, such as a city or airport bounding box. No GPS is sent to an
online geocoding service. The example regions are illustrative, not national borders.

A photo can show where and when it was taken. Two observations in different
countries can bound a possible trip interval, but cannot establish an exact border
crossing date, immigration status, or continuous residence. Missing GPS, incorrect
camera clocks, absent time zones and gaps remain visible rather than guessed.

## Safety and limitations

- Moves use exclusive destination creation, hash verification, and source removal
  only after a verified copy. A failed copy leaves the source in place and may leave
  a partial destination for manual inspection. There is no automatic rollback.
- Use one process at a time; pause other writers/sync changes while applying. This
  is a personal utility, not a transactional filesystem or protection against hostile
  concurrent filesystem manipulation.
- Symlinks, directory junctions, hidden/underscore folders and `[FORMAT]` are skipped.
- Duplicate removal is permanent; keep a backup. Similar-looking images are never
  treated as duplicates. AAE deletion removes Apple's edit instructions, not images.
- OCR requires manual review, especially for sparse chat screenshots. See
  [text review](docs/text-review.md). Optional detector dependencies are not needed
  for metadata search or folder organization.
- Metadata indexes and review outputs contain private paths, GPS and thumbnails.
  Keep them outside the repository. Ignore rules are a convenience, not a privacy audit.

## Development and remaining work

```sh
python -m unittest discover -s tests -v
```

Tests use temporary, synthetic files and do not scan a real library. See
[release checklist and roadmap](docs/ROADMAP.md) for implemented safeguards,
known limits and future work, in English and Chinese. MIT license.

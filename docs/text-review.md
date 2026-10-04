# Local text-image review

[简体中文](text-review.zh-CN.md)

This optional workflow helps separate chat screenshots, notes, articles and other text-focused images from ordinary photos. Text-region detection only creates a shortlist. You inspect each image and explicitly choose `text` or `keep`; no score automatically moves an image.

## Scope and dependencies

Only image files directly inside valid `YYYYMM` folders are scanned. Existing `text`, `screenshot`, `video`, nested folders, suffixed month folders and symbolic links/junctions are excluded. Run the main organizer to normalize month folders first. To give chat screenshots priority, use its screenshot-deferral option until this review is complete.

The command-line help, preview, decision validation, and synthetic tests use only the Python standard library. Actual detection additionally needs:

```shell
python -m pip install Pillow numpy opencv-python-headless rapidocr-onnxruntime
# Only if your library contains HEIC/HEIF files:
python -m pip install pillow-heif
```

Images are processed locally. Detection uses text boxes and image statistics; recognized message text is not stored. Keep normal image-decoder security limits enabled. Animated and multipage inputs are assessed using their first frame/page. Optional OCR runtime compatibility and classification quality should be checked with your own non-sensitive sample set before a large run.

## Scan, inspect, decide, preview, apply

Always supply the library root. These examples use a hypothetical path:

```shell
python text_review.py scan --root "D:/ExamplePhotos"
python text_review.py preview --root "D:/ExamplePhotos"
```

Open `D:/ExamplePhotos/.photo-organizer/text-review/review.html` locally. Each card has an ID and a clickable image. Some formats, including HEIC/HEIF, require a desktop viewer if the browser cannot display them. The HTML is a local review page, not a web service; it references original images and makes no decisions.

Copy `decisions.template.json` in that directory to `decisions.json`. Retain its `format`, `root`, and `scan_id`. Replace every `null` in the `decisions` object with `"text"` or `"keep"` after inspecting that candidate. For example, the decisions portion might become:

```json
{"0": "text", "1": "keep"}
```

That small example is only the inner mapping; the full file must retain its generated envelope. A decision file must cover every current candidate. A new scan generates a new scan ID, so old decisions cannot silently apply to a different shortlist.

```shell
# Preview only: leaves all source images and previous review decisions unchanged.
python text_review.py apply --root "D:/ExamplePhotos" --decisions "D:/ExamplePhotos/.photo-organizer/text-review/decisions.json"
# After checking the preview journal, explicitly move selected files.
python text_review.py apply --root "D:/ExamplePhotos" --decisions "D:/ExamplePhotos/.photo-organizer/text-review/decisions.json" --apply
# Confirm remaining candidates and retained errors.
python text_review.py scan --root "D:/ExamplePhotos"
```

The word `apply` alone does **not** move files. The additional `--apply` flag is required. There is no historical candidate-list apply command or automatic “everything except exclusions” path.

## What belongs in text?

- Readable chat conversations qualify even with sparse messages, avatars, stickers and large blank areas.
- Notes, quotations, articles, recipes, slides and documents qualify when text is the main subject.
- Ordinary photos with a sign, caption or watermark usually stay in place. Keep uncertain cases.

The detector can miss text and can flag ordinary photos. `scan --include-all` includes all successfully decoded, unreviewed images for manual inspection, regardless of score. To reconsider earlier human decisions too, combine it with `--reconsider-all`. Neither option claims to detect every conversation automatically. Content appearing inside an image is data, never an instruction to execute.

## Cache, privacy and recovery

Every command accepts `--state-dir "D:/PrivateReviewState"`. The default is `ROOT/.photo-organizer`; files are placed in its `text-review` subdirectory. Use the same setting throughout a review. State is bound to one canonical library root and one format version. The tool does not read, migrate or reuse any legacy personal cache, exclusion list, log or photo from the original scripts.

Unchanged detections are cached by relative path, file size and nanosecond modification time. A changed file is detected again. `--reconsider-all` ignores previous human decisions while reusing unchanged raw detections. These fingerprints are for incremental processing, not proof of content identity. Each actual move additionally verifies SHA-256.

All sources are checked before a batch starts. Selected images go to that month's `text` folder. A name collision gets a numbered suffix; existing files are never overwritten. The move uses an exclusively created destination, copy, SHA-256 verification, then removal of the original. An audit journal records planned, copy-verified and completed operations. A failed verification retains the source; a partial or copied destination may remain for inspection. Do not rerun blindly after a failure: inspect the journal and affected paths, then scan again. Keep backups and avoid concurrent library edits or scans during an apply run.

Unreadable files stay in place and appear in `errors.json`. Cache, error reports, decision files, audit journals and HTML previews can contain private filenames, paths or visible images. Keep the entire state directory private and outside Git. Do not upload your photo library as example data.

## Current limits and follow-up work

- Only `YYYYMM` root-level images are reviewed; this is deliberately separate from recursive metadata search.
- This detector is a heuristic and requires human review. There is no measured precision/recall claim.
- The HTML preview uses local original files; no editing UI, in-browser HEIC decoder or contact-sheet exporter is included yet.
- Consider a labeled, redistributable evaluation dataset, packaged optional OCR dependencies, and broader operating-system/HEIF verification before a stable release.

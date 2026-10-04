# Offline photo metadata search

[简体中文](metadata.zh-CN.md) · [Project README](../README.md)

Index EXIF metadata locally, find photos by location or date, and export a sequence of location observations. This can help reconstruct a personal travel history from photos you own. **It does not produce official immigration records or exact border-crossing dates.**

No photos or coordinates are sent to an external service. Indexing reads photos without moving, modifying, or deleting them. Search and timeline use the local index without opening the original photos.

## Install

Python 3.12 or later:

```sh
python -m pip install ".[metadata]"
```

For HEIC/HEIF files, install the optional decoder:

```sh
python -m pip install ".[heif]"
```

The commands below work from a source checkout. An installed package also exposes `photo-metadata` with the same arguments. `search` and `timeline` use only Python's standard library; only `index` imports Pillow.

## 1. Define local search regions

Copy `examples/places.example.json` to a private local file, such as `private-data/places.json`. Edit its rectangles to fit your own search needs:

```json
{
  "places": [
    {
      "id": "sydney-example",
      "name": "Sydney example region",
      "name_zh": "悉尼示例区域",
      "country": "AU",
      "bbox": [150.5, -34.3, 151.5, -33.3]
    }
  ]
}
```

`bbox` means `[west, south, east, north]` in decimal degrees. All bounds are inclusive. `west > east` describes a rectangle crossing the antimeridian. IDs must be unique. `name_zh` is optional; `id`, `name`, `country`, and `bbox` are required.

The supplied Sydney, Melbourne, and Shenzhen rectangles are illustrative city regions. They do not cover entire countries and are not authoritative administrative or national boundaries. `country` is a user-defined grouping label; use any consistent labels. Coordinates outside every configured rectangle remain unclassified. There is no online reverse geocoding, global country lookup, or inferred location for photos without GPS.

If rectangles overlap, the smallest spherical rectangle wins; equal areas are ordered by ID. The index retains every matching ID and country label. A photo with conflicting country labels remains searchable, but it is excluded from country-change inference in the timeline.

## 2. Build an index

```sh
python photo_metadata.py index "D:/MyPhotos" --places private-data/places.json --output private-data/index.jsonl
```

The example path is a placeholder: choose your own library. `--places` is optional. Without it, GPS radius search still works, but no place or country labels are assigned.

Supported image extensions: JPG, JPEG, PNG, TIFF/TIF, WebP, HEIC, and HEIF. Decoder availability and the presence of EXIF metadata determine what can be read. Videos, RAW files, XMP sidecars, image-content recognition, and OCR are outside this metadata feature.

Traversal skips symlinks and junctions. `.git` is always skipped. Hidden files, hidden directories, and directories beginning with `_` are skipped by default; `--include-hidden` includes these except `.git`. A root that is itself a symlink or junction is rejected.

Each line of the JSONL index represents one discovered image and includes:

- A path relative to the supplied root, filename, extension, and pixel dimensions.
- Camera make/model when present.
- EXIF `DateTimeOriginal` and its `OffsetTimeOriginal`, when valid.
- GPS latitude/longitude, optional altitude, and GPS UTC timestamp.
- The selected local region, all matching region IDs/country labels, and the selection rule.
- `capture_time_source`, `timezone_status`, and explicit `missing`, `warnings`, and `errors` fields.

When `DateTimeOriginal` is absent or invalid, a valid `GPSDateStamp` plus `GPSTimeStamp` can supply a separately labeled UTC time. A GPS clock can describe a GPS fix rather than the exact shutter instant. EXIF modification time is not treated as capture time. A valid original clock with a malformed offset is retained without a timezone and with a warning. A GPS timestamp does not silently supply a timezone to an original local clock.

There is **no default filesystem-time fallback**. `--mtime-fallback` explicitly permits UTC file modification time when capture metadata is unavailable. Such records use `filesystem:mtime_fallback` and a warning; they do not establish when a photo was taken. Timeline generation excludes them unless `--include-mtime` is also requested.

Unreadable or unsupported images remain in the index with errors. The final status goes to standard error; exit code `2` means there were image or directory scan errors, even if a partial index was successfully written. Missing EXIF metadata alone is not an error. Review the status before treating an index as complete.

## 3. Search

All filters combine with AND:

```sh
# Local calendar days, inclusive at both ends
python photo_metadata.py search private-data/index.jsonl --after 2024-01-01 --before 2024-12-31

# Within 25 km of an illustrative Sydney coordinate
python photo_metadata.py search private-data/index.jsonl --near -33.86 151.21 25

# User-defined country/place labels and filename or camera substrings
python photo_metadata.py search private-data/index.jsonl --country AU --place Sydney --camera Example --filename holiday

# Export Chinese CSV headers and names
python photo_metadata.py search private-data/index.jsonl --country AU --format csv --lang zh --output private-data/au-photos.csv
```

`--near LAT LON KM` uses great-circle distance. Coordinates must be finite and in range; the radius must be finite and non-negative. `--country` matches a complete label, ignoring case. `--place` searches the selected region ID and English/Chinese names; camera and filename searches are also case-insensitive substrings. It does not search the names of overlapping, non-selected regions.

Date-only boundaries use the photo's recorded local calendar date. Timestamp boundaries may include offsets, for example `2024-03-01T00:00:00+11:00`. Offset-aware photo timestamps are compared as real instants only against offset-aware timestamp boundaries. A timestamp with an unknown timezone is compared only against a boundary that also lacks an offset. Incompatible timezone records are skipped and counted; the tool does not assume your computer's timezone. Use date-only filters if you intend a local-calendar search across mixed timezone metadata.

JSON output contains full matching index records, count, and skipped-record statistics. CSV output contains the main search fields; detailed errors and overlapping-region information remain in JSON. No matches is a successful search with an empty result. Search order follows the index order, not chronological or distance order.

## 4. Export travel observations

```sh
python photo_metadata.py timeline private-data/index.jsonl --format csv --lang en --output private-data/travel-en.csv
python photo_metadata.py timeline private-data/index.jsonl --format csv --lang zh --output private-data/travel-zh.csv
python photo_metadata.py timeline private-data/index.jsonl --output private-data/travel.json
```

The report contains dated photo observations and possible movement intervals between adjacent, usable country observations with different labels. Consecutive observations within one country update the last known observation. Unclassified or conflicting-region observations between countries are counted, but not assigned a country. Metadata warnings and errors remain visible in JSON and timeline CSV. Missing or invalid capture times are counted and omitted from the sequence.

For example, a photo labeled `AU` on March 1 and the next usable observation labeled `CN` on March 5 suggest **possible movement sometime between those observations**. The result cannot say that departure or arrival occurred on March 1 or March 5. Missing photos can hide intermediate countries, multiple trips, stopovers, or returns; location metadata can be absent, edited, inaccurate, or associated with a shared photo. A photo does not establish who was physically present.

When every included timestamp has a known offset, observations are ordered by UTC instant. Otherwise, the report uses recorded local capture times and warns that cross-timezone order is uncertain. It never infers a timezone from coordinates. Different country labels at identical sortable timestamps are flagged as simultaneous or conflicting observations. Conflicting overlapping-region country labels are not used to infer movement at all. Use the original metadata and independent records when checking a travel history.

## Output and privacy

- JSON and JSONL are UTF-8; saved CSV files include a UTF-8 BOM for spreadsheet compatibility. `--lang en` or `--lang zh` changes human-readable notices, CSV headers, and available place names. JSON field names and status codes stay stable in English.
- Use `.jsonl` for an index, `.json` for JSON output, and `.csv` with `--format csv`. The extension must match the requested format.
- Existing outputs are preserved unless `--overwrite` is explicitly supplied. Search/timeline cannot overwrite their input index. Output paths and their ancestor directories must not be symlinks or junctions. Media extensions are rejected as output paths.
- CSV strings that resemble spreadsheet formulas are escaped. Numeric coordinates remain numeric.
- Keep indexes, CSV reports, real photos, real place definitions, and travel records outside public Git history. They can contain precise locations, timestamps, and filenames even when the photos themselves are absent. The checked-in region example and tests contain no personal observations.
- This is a local command-line MVP. There is no map UI, incremental database, face/subject search, airport detection, automatic country boundary dataset, or official immigration-system integration. Changes to region definitions require re-indexing.

## Test without private data

```sh
python -m unittest discover -s tests -p test_metadata.py -v
```

Tests create small synthetic images and records in temporary directories. They exercise EXIF/GPS decoding, region overlap, distance and date filters, timezone uncertainty, output protections, and an index → search → timeline CLI round trip. Pillow-specific tests are skipped if Pillow is unavailable; all other tests use only the standard library.

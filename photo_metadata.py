"""Offline photo metadata indexing, search, and travel observation timelines.

Only ``index`` imports Pillow. All commands are read-only with respect to photos.
Locations come from user-defined rectangles, never an online geocoding service.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic", ".heif"}
NOTICE = {
    "en": "Photo observations are not official immigration records. A change of country label only suggests possible movement between observations; it cannot establish an exact border-crossing date. Labels are user-defined regions, not authoritative national boundaries.",
    "zh": "照片观察不是官方出入境记录。国家标签变化仅提示两次观察之间可能发生移动，不能确定准确过境日期。地点标签来自用户定义的区域，并非权威国界。",
}


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("number must be finite")
    return number


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO 8601 string")
    value = value.strip()
    # Require a clock; accepting a date alone would invent a midnight exposure.
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
        raise ValueError("timestamp needs a date and time")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def timezone_status(dt):
    if dt is None:
        return "missing"
    return "known_offset" if dt.utcoffset() is not None else "unknown"


def parse_boundary(value):
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        return parse_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD or an ISO timestamp, optionally with a UTC offset") from exc


def _boundary_edge(value, upper=False):
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.max if upper else time.min)


def validate_boundaries(after, before):
    if after is None or before is None:
        return
    if isinstance(after, datetime) and isinstance(before, datetime):
        if (after.utcoffset() is None) != (before.utcoffset() is None):
            raise ValueError("--after and --before timestamps must both have offsets, or both omit offsets")
        if after > before:
            raise ValueError("--after must not be later than --before")
    else:
        # Dates express the photo's local calendar day, not an invented timezone.
        if _boundary_edge(after).replace(tzinfo=None) > _boundary_edge(before, True).replace(tzinfo=None):
            raise ValueError("--after must not be later than --before")


def validate_near(values):
    if values is None:
        return None
    lat, lon, radius = [finite_float(v) for v in values]
    if not -90 <= lat <= 90 or not -180 <= lon <= 180 or radius < 0:
        raise ValueError("--near requires LAT [-90,90], LON [-180,180], and a non-negative radius in km")
    return lat, lon, radius


def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance; clamp rounding error at antipodal points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def load_places(path):
    if not path:
        return []
    with Path(path).open(encoding="utf-8-sig") as handle:
        data = json.load(handle)
    places = data.get("places") if isinstance(data, dict) else None
    if not isinstance(places, list):
        raise ValueError("places file must be an object containing a 'places' array")
    result, ids = [], set()
    for item in places:
        if not isinstance(item, dict):
            raise ValueError("each place must be an object")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("id", "name", "country")):
            raise ValueError("each place needs non-empty id, name, and country strings")
        if item["id"] in ids:
            raise ValueError("duplicate place id: " + item["id"])
        ids.add(item["id"])
        bbox = item.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("bbox must be [west, south, east, north]")
        west, south, east, north = [finite_float(v) for v in bbox]
        if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south < north <= 90):
            raise ValueError("invalid bbox coordinates")
        width = east - west if east >= west else 360 + east - west
        if width <= 0:
            raise ValueError("bbox must have positive longitude width")
        result.append({**item, "bbox": [west, south, east, north], "_area": width * (math.sin(math.radians(north)) - math.sin(math.radians(south)))})
    return result


def match_place(lat, lon, places):
    matches = []
    for place in places:
        west, south, east, north = place["bbox"]
        inside_longitude = west <= lon <= east if west <= east else lon >= west or lon <= east
        if south <= lat <= north and inside_longitude:
            matches.append(place)
    if not matches:
        return None
    matches.sort(key=lambda p: (p["_area"], p["id"]))
    first = matches[0]
    return {
        "id": first["id"], "name": first["name"], "name_zh": first.get("name_zh", first["name"]),
        "country": first["country"], "source": "user_defined_bbox",
        "matched_ids": [p["id"] for p in matches],
        "matched_countries": list(dict.fromkeys(p["country"] for p in matches)),
        "selection_rule": "smallest_spherical_rectangle_then_id",
    }


def _text(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip("\x00 ") if value is not None else None


def _exif_capture(value, offset=None):
    value = _text(value)
    if not value:
        return None
    dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    offset = _text(offset)
    if offset:
        if not re.fullmatch(r"[+-]\d{2}:\d{2}", offset):
            raise ValueError("invalid OffsetTimeOriginal")
        hour, minute = int(offset[1:3]), int(offset[4:6])
        if hour > 23 or minute > 59:
            raise ValueError("invalid OffsetTimeOriginal")
        sign = 1 if offset[0] == "+" else -1
        dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=hour, minutes=minute)))
    return dt


def _coordinate(dms, reference, maximum):
    if not isinstance(dms, (tuple, list)) or len(dms) != 3:
        raise ValueError("GPS coordinate needs degrees, minutes, seconds")
    degrees, minutes, seconds = [finite_float(value) for value in dms]
    if degrees < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError("invalid GPS coordinate components")
    ref = (_text(reference) or "").upper()
    if ref not in ({"N", "S"} if maximum == 90 else {"E", "W"}):
        raise ValueError("missing or invalid GPS hemisphere")
    result = degrees + minutes / 60 + seconds / 3600
    if result > maximum:
        raise ValueError("GPS coordinate outside valid range")
    return -result if ref in {"S", "W"} else result


def _gps_timestamp(gps):
    stamp, clock = _text(gps.get(29)), gps.get(7)
    if not stamp or clock is None:
        return None
    if not isinstance(clock, (tuple, list)) or len(clock) != 3:
        raise ValueError("invalid GPS timestamp")
    hours, minutes, seconds = [finite_float(v) for v in clock]
    if hours != int(hours) or minutes != int(minutes) or not 0 <= hours < 24 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError("invalid GPS clock")
    day = datetime.strptime(stamp, "%Y:%m:%d").replace(tzinfo=timezone.utc)
    return day + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def metadata_from_exif(exif, exif_ifd=None, gps_ifd=None):
    """Extract plain JSON metadata, also usable with synthetic EXIF dictionaries."""
    exif_ifd = exif_ifd or {}
    gps_ifd = gps_ifd or {}
    result = {
        "captured_at": None, "capture_time_source": None, "timezone_status": "missing",
        "camera": {"make": _text(exif.get(271)), "model": _text(exif.get(272))},
        "gps": None, "warnings": [],
    }
    dt = None
    original = exif_ifd.get(36867, exif.get(36867))
    offset = exif_ifd.get(36881, exif.get(36881))
    if original:
        try:
            dt = _exif_capture(original, offset)
        except (ValueError, TypeError, OverflowError) as exc:
            result["warnings"].append("DateTimeOriginal/OffsetTimeOriginal: " + str(exc))
            # A malformed offset need not discard an otherwise valid wall-clock time.
            try:
                dt = _exif_capture(original)
            except (ValueError, TypeError, OverflowError):
                pass
        if dt is not None:
            result["capture_time_source"] = "exif:DateTimeOriginal"
    gps_time = None
    if gps_ifd:
        try:
            gps_time = _gps_timestamp(gps_ifd)
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            result["warnings"].append("GPS timestamp: " + str(exc))
        try:
            lat = _coordinate(gps_ifd.get(2), gps_ifd.get(1), 90)
            lon = _coordinate(gps_ifd.get(4), gps_ifd.get(3), 180)
            result["gps"] = {"latitude": lat, "longitude": lon, "source": "exif:GPSInfo", "timestamp_utc": gps_time.isoformat() if gps_time else None}
            if gps_ifd.get(6) is not None:
                altitude = finite_float(gps_ifd[6])
                reference = gps_ifd.get(5, 0)
                below = reference == 1 or reference == b"\x01"
                result["gps"]["altitude_m"] = -altitude if below else altitude
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            result["warnings"].append("GPS coordinates/altitude: " + str(exc))
    if gps_time:
        result["gps_timestamp_utc"] = gps_time.isoformat()
        if dt is None:
            dt = gps_time
            result["capture_time_source"] = "exif:GPSDateStamp+GPSTimeStamp"
    if dt is not None:
        result["captured_at"] = dt.isoformat()
        result["timezone_status"] = timezone_status(dt)
    return result


def _is_link(path):
    if path.is_symlink():
        return True
    if hasattr(os.path, "isjunction"):
        return os.path.isjunction(path)
    if os.name == "nt":
        try:
            # Python before 3.12 lacks isjunction; do not confuse OneDrive cloud
            # placeholders with the Windows mount-point reparse tag.
            return getattr(path.lstat(), "st_reparse_tag", 0) == 0xA0000003
        except FileNotFoundError:
            return False
    return False


def iter_media(root, include_hidden=False, scan_errors=None):
    root = Path(root)
    if _is_link(root):
        raise ValueError("root must not be a symlink or junction")
    errors = scan_errors if scan_errors is not None else []

    def walk(directory):
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            errors.append({"path": str(directory.relative_to(root)), "error": str(exc)})
            return
        for path in entries:
            try:
                if _is_link(path):
                    continue
                hidden = bool(getattr(path.stat(), "st_file_attributes", 0) & 2)
                if path.is_dir():
                    if path.name == ".git" or (not include_hidden and (hidden or path.name.startswith((".", "_")))):
                        continue
                    yield from walk(path)
                elif path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
                    if include_hidden or (not hidden and not path.name.startswith(".")):
                        yield path
            except OSError as exc:
                errors.append({"path": str(path.relative_to(root)), "error": str(exc)})

    yield from walk(root)


def load_image_reader():
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("index requires Pillow: python -m pip install Pillow") from exc
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image


def read_metadata(path, root, image_reader, places=(), mtime_fallback=False):
    path, root = Path(path), Path(root)
    record = {
        "schema_version": 1, "path": path.relative_to(root).as_posix(), "filename": path.name,
        "extension": path.suffix.lower(), "captured_at": None, "capture_time_source": None,
        "timezone_status": "missing", "camera": {"make": None, "model": None},
        "width": None, "height": None, "gps": None, "place": None, "warnings": [], "errors": [],
    }
    try:
        with image_reader.open(path) as image:
            record["width"], record["height"] = image.size
            exif = image.getexif()
            sub_ifds = {}
            for tag in (34665, 34853):
                try:
                    sub_ifds[tag] = exif.get_ifd(tag) if hasattr(exif, "get_ifd") else {}
                except (ValueError, TypeError, OSError, KeyError) as exc:
                    sub_ifds[tag] = {}
                    record["warnings"].append("EXIF IFD " + str(tag) + ": " + str(exc))
            metadata = metadata_from_exif(exif, sub_ifds[34665], sub_ifds[34853])
            metadata["warnings"] = record["warnings"] + metadata["warnings"]
            record.update(metadata)
    except Exception as exc:
        # A corrupt/unsupported photo must remain visible in the index.
        record["errors"].append(type(exc).__name__ + ": " + str(exc))
    if not record["captured_at"] and mtime_fallback:
        try:
            record["captured_at"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            record["capture_time_source"] = "filesystem:mtime_fallback"
            record["timezone_status"] = "known_offset"
            record["warnings"].append("Filesystem modification time is not evidence of exposure time.")
        except OSError as exc:
            record["errors"].append("mtime: " + str(exc))
    if record["gps"]:
        record["place"] = match_place(record["gps"]["latitude"], record["gps"]["longitude"], places)
        if record["place"] and len(record["place"]["matched_countries"]) > 1:
            record["warnings"].append("Overlapping regions have conflicting country labels; smallest region wins.")
    record["missing"] = [key for key in ("captured_at", "gps", "place") if not record[key]]
    if not any(record["camera"].values()):
        record["missing"].append("camera")
    return record


def load_records(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                    raise ValueError("record needs a string path")
                yield record
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid JSONL record on line {number}: {exc}") from exc


def search_records(records, *, after=None, before=None, near=None, camera=None, filename=None, place=None, country=None):
    validate_boundaries(after, before)
    near = validate_near(near)
    found, skipped = [], {"missing_or_invalid_time": 0, "incomparable_timezone": 0, "invalid_gps": 0}
    for record in records:
        if camera:
            camera_info = record.get("camera") or {}
            if camera.casefold() not in " ".join(str(camera_info.get(k) or "") for k in ("make", "model")).casefold():
                continue
        if filename and filename.casefold() not in str(record.get("filename", Path(record["path"]).name)).casefold():
            continue
        label = record.get("place") or {}
        if place and place.casefold() not in " ".join(str(label.get(k) or "") for k in ("id", "name", "name_zh")).casefold():
            continue
        if country and country.casefold() != str(label.get("country") or "").casefold():
            continue
        if after is not None or before is not None:
            try:
                dt = parse_timestamp(record.get("captured_at"))
            except (TypeError, ValueError):
                skipped["missing_or_invalid_time"] += 1
                continue
            comparable, matches = True, True
            for boundary, lower in ((after, True), (before, False)):
                if boundary is None:
                    continue
                if isinstance(boundary, datetime):
                    if (boundary.utcoffset() is None) != (dt.utcoffset() is None):
                        comparable = False
                        break
                    value = dt
                else:
                    value = dt.date()
                if (lower and value < boundary) or (not lower and value > boundary):
                    matches = False
            if not comparable:
                skipped["incomparable_timezone"] += 1
                continue
            if not matches:
                continue
        distance = None
        if near:
            gps = record.get("gps")
            if not gps:
                continue
            try:
                lat, lon = finite_float(gps["latitude"]), finite_float(gps["longitude"])
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError("invalid coordinates")
                distance = distance_km(near[0], near[1], lat, lon)
            except (ValueError, TypeError, KeyError, OverflowError):
                skipped["invalid_gps"] += 1
                continue
            if distance > near[2]:
                continue
        found.append({**record, **({"distance_km": round(distance, 6)} if distance is not None else {})})
    return found, skipped


def build_timeline(records, lang="en", include_mtime=False):
    observations, missing_time, excluded_mtime = [], 0, 0
    for record in records:
        if record.get("capture_time_source") == "filesystem:mtime_fallback" and not include_mtime:
            excluded_mtime += 1
            continue
        try:
            dt = parse_timestamp(record.get("captured_at"))
        except (ValueError, TypeError):
            missing_time += 1
            continue
        label = record.get("place") or {}
        label_status = "conflicting_regions" if len(set(label.get("matched_countries", []))) > 1 else "user_defined" if label.get("country") else "missing"
        observations.append({
            "path": record["path"], "captured_at": dt.isoformat(),
            "capture_time_source": record.get("capture_time_source"),
            "timezone_status": timezone_status(dt), "gps": record.get("gps"),
            "place": record.get("place"), "country_label_status": label_status, "metadata_warnings": record.get("warnings", []),
            "metadata_errors": record.get("errors", []), "_datetime": dt,
        })
    all_aware = bool(observations) and all(o["_datetime"].utcoffset() is not None for o in observations)
    order_basis = "utc_instant" if all_aware else "local_capture_time"
    observations.sort(key=lambda o: (o["_datetime"].astimezone(timezone.utc) if all_aware else o["_datetime"].replace(tzinfo=None), o["path"]))
    warnings = []
    if observations and not all_aware:
        warnings.append("部分照片缺少时区；按照片本地时间排序，跨时区先后顺序可能不准确。" if lang == "zh" else "Some photos lack timezone offsets. Ordering uses local capture time; cross-timezone chronology may be uncertain.")
    if excluded_mtime:
        warnings.append((f"已排除 {excluded_mtime} 条文件修改时间记录。" if lang == "zh" else f"Excluded {excluded_mtime} filesystem modification-time records."))
    if include_mtime:
        warnings.append("已允许文件修改时间参与排序；它不能证明拍摄时间。" if lang == "zh" else "Filesystem modification times are allowed in ordering; they do not establish exposure time.")
    conflicts = sum(o["country_label_status"] == "conflicting_regions" for o in observations)
    if conflicts:
        warnings.append(f"{conflicts} 条观察的区域国家标签冲突；保留照片观察，但不用于国家变化判断。" if lang == "zh" else f"{conflicts} observations have conflicting region country labels; retained as observations but excluded from country-change inference.")
    transitions, previous, unknown_between = [], None, 0
    for observation in observations:
        label = observation["place"] or {}
        if observation["country_label_status"] != "user_defined":
            unknown_between += 1
            continue
        if previous is not None and previous["place"]["country"] != label["country"]:
            same_time = previous["_datetime"] == observation["_datetime"] if all_aware else previous["_datetime"].replace(tzinfo=None) == observation["_datetime"].replace(tzinfo=None)
            transitions.append({
                "kind": "possible_movement_interval", "from_country": previous["place"]["country"], "to_country": label["country"],
                "from_observed_at": previous["captured_at"], "to_observed_at": observation["captured_at"],
                "from_path": previous["path"], "to_path": observation["path"],
                "unclassified_observations_between": unknown_between,
                "chronology_status": "simultaneous_or_conflicting_observations" if same_time else "offsets_known" if all_aware else "uncertain_local_time_order",
                "basis": "photo_observations_only",
            })
        previous, unknown_between = observation, 0
    for observation in observations:
        observation.pop("_datetime")
    return {
        "schema_version": 1, "notice": NOTICE[lang], "order_basis": order_basis,
        "warnings": warnings, "omitted_missing_or_invalid_time": missing_time,
        "excluded_mtime_records": excluded_mtime, "conflicting_region_observations": conflicts,
        "observations": observations, "possible_movements": transitions,
    }


def _csv_cell(value):
    if isinstance(value, (int, float)):
        return value
    value = "" if value is None else str(value)
    # Protect spreadsheet users from formula-like filenames and editable labels.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def _csv_text(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
    return stream.getvalue()


def search_csv(records, lang):
    headers = ["路径", "拍摄时间", "时间来源", "时区状态", "地点", "国家标签", "纬度", "经度", "相机", "距离公里"] if lang == "zh" else ["Path", "Captured at", "Time source", "Timezone status", "Place", "Country label", "Latitude", "Longitude", "Camera", "Distance km"]
    rows = []
    for record in records:
        label, gps, camera = record.get("place") or {}, record.get("gps") or {}, record.get("camera") or {}
        rows.append([record["path"], record.get("captured_at"), record.get("capture_time_source"), record.get("timezone_status"), label.get("name_zh" if lang == "zh" else "name"), label.get("country"), gps.get("latitude"), gps.get("longitude"), " ".join(str(camera.get(k) or "") for k in ("make", "model")).strip(), record.get("distance_km")])
    return _csv_text(headers, rows)


def timeline_csv(timeline, lang):
    headers = ["类型", "观察时间起", "观察时间止", "国家标签起", "国家标签止", "地点", "照片路径起", "照片路径止", "时间来源", "时区或顺序状态", "间隔内无地点标签照片数", "说明"] if lang == "zh" else ["Type", "Observed from", "Observed to", "From country label", "To country label", "Place", "From photo", "To photo", "Time source", "Timezone or chronology status", "Unclassified observations between", "Note"]
    rows = [["说明" if lang == "zh" else "notice", "", "", "", "", "", "", "", "", "", "", timeline["notice"]]]
    for warning in timeline["warnings"]:
        rows.append(["警告" if lang == "zh" else "warning", "", "", "", "", "", "", "", "", "", "", warning])
    rows.append(["统计" if lang == "zh" else "summary", "", "", "", "", "", "", "", "", timeline["order_basis"], "", (f"缺失或无效拍摄时间：{timeline['omitted_missing_or_invalid_time']}；排除文件修改时间：{timeline['excluded_mtime_records']}" if lang == "zh" else f"Missing/invalid capture time: {timeline['omitted_missing_or_invalid_time']}; excluded modification times: {timeline['excluded_mtime_records']}")])
    for observation in timeline["observations"]:
        label = observation["place"] or {}
        notes = [*observation.get("metadata_warnings", []), *observation.get("metadata_errors", [])]
        if observation["country_label_status"] == "conflicting_regions":
            notes.append("国家标签冲突，不用于国家变化判断" if lang == "zh" else "Conflicting country labels; excluded from country-change inference")
        rows.append(["照片观察" if lang == "zh" else "observation", observation["captured_at"], "", label.get("country"), "", label.get("name_zh" if lang == "zh" else "name"), observation["path"], "", observation["capture_time_source"], observation["timezone_status"], "", " | ".join(notes)])
    for movement in timeline["possible_movements"]:
        rows.append(["可能移动区间" if lang == "zh" else "possible_movement_interval", movement["from_observed_at"], movement["to_observed_at"], movement["from_country"], movement["to_country"], "", movement["from_path"], movement["to_path"], movement["basis"], movement["chronology_status"], movement["unclassified_observations_between"], timeline["notice"]])
    return _csv_text(headers, rows)


def write_output(path, chunks, overwrite=False, csv_output=False):
    """Stream to a new local file; replace an existing output only on explicit opt-in."""
    if path is None:
        for chunk in chunks:
            sys.stdout.write(chunk)
        return
    target = Path(path)
    for candidate in (target.absolute(), *target.absolute().parents):
        if _is_link(candidate):
            raise ValueError("output and its parent directories must not be symlinks or junctions")
    if target.exists() and not overwrite:
        raise ValueError("output exists; choose another path or pass --overwrite")
    if target.suffix.lower() not in {".json", ".jsonl", ".csv"}:
        raise ValueError("output must use .json, .jsonl, or .csv; media output paths are forbidden")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".photo-metadata-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig" if csv_output else "utf-8", newline="") as handle:
            for chunk in chunks:
                handle.write(chunk)
        if overwrite:
            os.replace(temporary, target)
        else:
            # Exclusive creation also closes the race between the initial check and commit.
            with open(temporary, "rb") as source, target.open("xb") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    destination.write(chunk)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_parser():
    parser = argparse.ArgumentParser(description="Offline photo metadata search / 离线照片元数据搜索")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Read metadata; never change photos / 读取元数据，不修改照片")
    index.add_argument("root", type=Path, help="photo directory / 照片目录")
    index.add_argument("--output", type=Path, required=True, help="local JSONL index / 本地 JSONL 索引")
    index.add_argument("--places", type=Path, help="local region definitions / 本地地点区域定义")
    index.add_argument("--mtime-fallback", action="store_true", help="explicitly allow modification-time fallback / 明确允许文件修改时间回退")
    index.add_argument("--include-hidden", action="store_true", help="include hidden and underscore folders (never .git) / 包含隐藏与下划线目录")
    search = commands.add_parser("search", help="Search an existing index / 搜索已有索引")
    search.add_argument("index", type=Path)
    search.add_argument("--after", type=parse_boundary, help="inclusive date/timestamp / 起始日期或时间，含边界")
    search.add_argument("--before", type=parse_boundary, help="inclusive date/timestamp / 结束日期或时间，含边界")
    search.add_argument("--near", nargs=3, metavar=("LAT", "LON", "KM"), help="GPS radius / 经纬度及半径公里")
    for name in ("camera", "filename", "place", "country"):
        search.add_argument("--" + name)
    timeline = commands.add_parser("timeline", help="Photo observations and possible movements / 照片观察与可能移动区间")
    timeline.add_argument("index", type=Path)
    timeline.add_argument("--include-mtime", action="store_true", help="include explicit modification-time fallbacks / 包含明确标记的文件修改时间")
    for command in (index, search, timeline):
        command.add_argument("--lang", choices=("en", "zh"), default="en", help="message/CSV language / 消息及 CSV 语言")
        command.add_argument("--overwrite", action="store_true", help="replace an existing output file / 替换已有输出文件")
    for command in (search, timeline):
        command.add_argument("--format", choices=("json", "csv"), default="json")
        command.add_argument("--output", type=Path, help="omit to write to standard output / 留空则输出到屏幕")
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            if not args.root.is_dir():
                raise ValueError("photo root is not an existing directory")
            if args.output.suffix.lower() != ".jsonl":
                raise ValueError("index output must end in .jsonl")
            places, image_reader = load_places(args.places), load_image_reader()
            stats, scan_errors = {"photos": 0, "with_errors": 0}, []

            def rows():
                for path in iter_media(args.root, args.include_hidden, scan_errors):
                    record = read_metadata(path, args.root, image_reader, places, args.mtime_fallback)
                    stats["photos"] += 1
                    stats["with_errors"] += bool(record["errors"])
                    yield json.dumps(record, ensure_ascii=False) + "\n"

            write_output(args.output, rows(), args.overwrite)
            message = {"索引完成": stats, "扫描错误": scan_errors} if args.lang == "zh" else {"indexed": stats, "scan_errors": scan_errors}
            print(json.dumps(message, ensure_ascii=False), file=sys.stderr)
            return 2 if scan_errors or stats["with_errors"] else 0
        if args.output is not None and args.output.resolve() == args.index.resolve():
            raise ValueError("output must not overwrite the input index")
        if args.output is not None and args.output.suffix.lower() != "." + args.format:
            raise ValueError("output extension must match --format: ." + args.format)
        if args.command == "search":
            records, skipped = search_records(load_records(args.index), after=args.after, before=args.before, near=args.near, camera=args.camera, filename=args.filename, place=args.place, country=args.country)
            output = search_csv(records, args.lang) if args.format == "csv" else json.dumps({"results": records, "count": len(records), "skipped": skipped}, ensure_ascii=False, indent=2) + "\n"
            print(("匹配照片数：" if args.lang == "zh" else "Matched photos: ") + str(len(records)), file=sys.stderr)
            if any(skipped.values()):
                print(("跳过记录：" if args.lang == "zh" else "Skipped records: ") + json.dumps(skipped), file=sys.stderr)
        else:
            result = build_timeline(load_records(args.index), args.lang, args.include_mtime)
            output = timeline_csv(result, args.lang) if args.format == "csv" else json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            print(NOTICE[args.lang], file=sys.stderr)
        write_output(args.output, [output], args.overwrite, args.format == "csv")
        return 0
    except (ValueError, OSError, OverflowError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

"""Optional, local text-region detector. Importing this module needs no OCR packages.

Scores are a candidate shortlist, never a decision to move a photograph.
No historical caches or personal paths are imported.
"""
from pathlib import Path

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.heic', '.heif',
                    '.avif', '.gif', '.tif', '.tiff'}
_engine = None


def is_candidate(record):
    """Broad heuristic that includes sparse chat screenshots for human review."""
    n, coverage, uniform, rows = (record.get(k, 0) for k in
                                  ('n', 'coverage', 'uniform', 'rows'))
    return ((n >= 6 and coverage >= .20 and rows >= .30 and uniform >= .40)
            or (n >= 4 and coverage >= .32 and uniform >= .32)
            or (n >= 12 and coverage >= .30 and rows >= .45)
            or (n >= 4 and coverage >= .10 and rows >= .16 and uniform >= .68)
            or (n >= 2 and coverage >= .015 and uniform >= .40)
            or (n >= 6 and coverage >= .06 and uniform >= .25)
            or (n >= 6 and coverage >= .24 and rows >= .42))


def scan_image(path):
    """Return region statistics, not recognized text; dependencies load on demand.

Exceptions are recorded by the caller and never cause an image to be removed.
HEIF support is optional and needed only for HEIC/HEIF input. Image safety limits
remain enabled. The first frame/page is used for animated or multipage formats.
"""
    global _engine
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError('OCR requires Pillow, numpy, opencv-python-headless and '
                           'rapidocr-onnxruntime; see docs/text-review.md') from exc
    path = Path(path)
    if path.suffix.lower() in {'.heic', '.heif'}:
        try:
            import pillow_heif
        except ImportError as exc:
            raise RuntimeError('HEIC/HEIF input additionally requires pillow-heif') from exc
        pillow_heif.register_heif_opener()
    if _engine is None:
        cv2.setNumThreads(1)
        _engine = RapidOCR(det_model_path=None, det_limit_side_len=960, det_limit_type='max',
                           use_angle_cls=False, intra_op_num_threads=1,
                           inter_op_num_threads=1)
    with Image.open(path) as original:
        original.draft('RGB', (1200, 1200))
        original.thumbnail((1200, 1200))
        im = ImageOps.exif_transpose(original).convert('RGB')
    array = np.asarray(im)
    height, width = array.shape[:2]
    small = np.asarray(im.resize((120, 120))).astype(np.int16)
    quantized = (small // 24).astype(np.int32)
    colors = quantized[:, :, 0] * 121 + quantized[:, :, 1] * 11 + quantized[:, :, 2]
    counts = np.bincount(colors.ravel())
    uniform = float(np.sort(counts)[-3:].sum() / colors.size)
    boxes, _ = _engine.text_detector(cv2.cvtColor(array, cv2.COLOR_RGB2BGR))
    mask = np.zeros((height, width), np.uint8)
    good = []
    if boxes is not None:
        for box in boxes:
            box_width = float(np.linalg.norm(box[1] - box[0]))
            box_height = float(np.linalg.norm(box[3] - box[0]))
            if box_width >= box_height * 1.5 and box_width >= width * .035:
                good.append(box)
                cv2.fillPoly(mask, [box.astype(np.int32)], 1)
    return {'n': len(good), 'coverage': float(mask.mean()),
            'rows': float((mask.mean(axis=1) > .18).mean()), 'uniform': uniform}

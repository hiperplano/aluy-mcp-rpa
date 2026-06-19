"""Vision backend — template matching, OCR, image diff."""

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

_HAS_CV2 = False
try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    pass

_HAS_PIL = False
try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    pass

_HAS_EASYOCR = False
_easyocr_reader = None
try:
    import easyocr
    _HAS_EASYOCR = True
except ImportError:
    pass


class VisionBackend:
    """Visual operations: template matching, OCR, diff."""

    def __init__(self, *, ocr_gpu: bool = False, ocr_canvas: int = 1280):
        if not _HAS_CV2:
            raise RuntimeError("OpenCV não instalado. pip install opencv-python-headless")
        if not _HAS_PIL:
            raise RuntimeError("Pillow não instalado. pip install Pillow")

        self._ocr_reader = None
        self._ocr_loaded = False
        self._ocr_gpu = ocr_gpu        # detectado: usa GPU se houver
        self._ocr_canvas = ocr_canvas  # detectado: capa o processamento p/ caber em 60s
        print(f"[rpa] Vision backend: OpenCV ok, EasyOCR={'sim' if _HAS_EASYOCR else 'não'} "
              f"(gpu={ocr_gpu}, canvas={ocr_canvas})", file=sys.stderr)

    # ── Template Matching ──────────────────────────────────

    def locate(self, screenshot_path: str, template_path: str,
               threshold: float = 0.8) -> Optional[dict]:
        """Find template in screenshot. Returns {x, y, width, height, confidence} or None."""
        screen = cv2.imread(screenshot_path)
        template = cv2.imread(template_path)

        if screen is None or template is None:
            raise ValueError("Não foi possível ler as imagens")

        h, w = template.shape[:2]
        if h > screen.shape[0] or w > screen.shape[1]:
            # template maior que tela → impossível
            return None

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            return None

        return {
            "x": max_loc[0] + w // 2,
            "y": max_loc[1] + h // 2,
            "width": w,
            "height": h,
            "confidence": float(max_val),
        }

    def locate_all(self, screenshot_path: str, template_path: str,
                   threshold: float = 0.8) -> list[dict]:
        """Find all matches of template in screenshot."""
        screen = cv2.imread(screenshot_path)
        template = cv2.imread(template_path)

        if screen is None or template is None:
            raise ValueError("Não foi possível ler as imagens")

        h, w = template.shape[:2]
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)
        matches = []
        for pt in zip(*locations[::-1]):  # (x, y)
            matches.append({
                "x": pt[0] + w // 2,
                "y": pt[1] + h // 2,
                "width": w,
                "height": h,
                "confidence": float(result[pt[1], pt[0]]),
            })
        # Non-max suppression simples
        return self._nms(matches, overlap_thresh=0.3)

    def _nms(self, matches: list[dict], overlap_thresh: float = 0.3) -> list[dict]:
        """Non-maximum suppression for template matches."""
        if not matches:
            return []
        boxes = np.array([[m["x"] - m["width"] // 2, m["y"] - m["height"] // 2,
                           m["x"] + m["width"] // 2, m["y"] + m["height"] // 2]
                          for m in matches])
        scores = np.array([m["confidence"] for m in matches])

        x1, y1 = boxes[:, 0], boxes[:, 1]
        x2, y2 = boxes[:, 2], boxes[:, 3]
        area = (x2 - x1 + 1) * (y2 - y1 + 1)
        idxs = np.argsort(scores)[::-1]

        keep = []
        while len(idxs) > 0:
            i = idxs[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[idxs[1:]])
            yy1 = np.maximum(y1[i], y1[idxs[1:]])
            xx2 = np.minimum(x2[i], x2[idxs[1:]])
            yy2 = np.minimum(y2[i], y2[idxs[1:]])
            w = np.maximum(0, xx2 - xx1 + 1)
            h = np.maximum(0, yy2 - yy1 + 1)
            overlap = (w * h) / area[idxs[1:]]
            idxs = idxs[1:][overlap < overlap_thresh]

        return [matches[i] for i in keep]

    # ── OCR ────────────────────────────────────────────────

    def _ensure_ocr(self):
        if self._ocr_loaded:
            return
        if _HAS_EASYOCR:
            os.environ.setdefault("EASYOCR_VERBOSE", "0")
            self._ocr_reader = easyocr.Reader(["pt", "en"], gpu=self._ocr_gpu, verbose=False)
        self._ocr_loaded = True

    def ocr(self, image_path: str, region: Optional[dict] = None) -> str:
        """Extract text from image using EasyOCR (primary) or Tesseract (fallback)."""
        img = Image.open(image_path)
        if region:
            x = region.get("x", 0)
            y = region.get("y", 0)
            w = region.get("width", img.width - x)
            h = region.get("height", img.height - y)
            img = img.crop((x, y, x + w, y + h))

        if _HAS_EASYOCR:
            try:
                self._ensure_ocr()
                tmp = tempfile.mktemp(suffix=".png")
                img.save(tmp)
                results = self._ocr_reader.readtext(tmp)
                os.unlink(tmp)
                if not results:
                    return "(nenhum texto detectado)"
                lines = []
                for bbox, text, conf in results:
                    lines.append(f"[{conf:.0%}] {text}")
                return "\n".join(lines)
            except Exception as e:
                print(f"[rpa] easyocr falhou: {e}", file=sys.stderr)

        # Tesseract fallback
        try:
            import pytesseract
            text = pytesseract.image_to_string(img, lang="por+eng")
            return text.strip() or "(nenhum texto detectado)"
        except ImportError:
            return "Erro: nem easyocr nem pytesseract disponíveis"
        except Exception as e:
            return f"Erro OCR: {e}"

    def ocr_tokens(self, image_path: str, region: Optional[dict] = None,
                   *, min_conf: float = 0.3) -> list:
        """Return ALL text tokens visible (scoped to region), each with its absolute
        click point: [{text, x, y, confidence}]. This is how a TEXT-only agent
        'sees' the screen — it can then click_text/click_at by what's listed."""
        if not _HAS_EASYOCR:
            return []
        import numpy as np
        self._ensure_ocr()
        img = Image.open(image_path).convert("RGB")
        ox, oy = 0, 0
        if region:
            ox = region.get("x", 0); oy = region.get("y", 0)
            w = region.get("width", img.width - ox); h = region.get("height", img.height - oy)
            img = img.crop((ox, oy, ox + w, oy + h))
        scale = 1
        if max(img.width, img.height) < 1100:
            scale = max(2, min(4, 1100 // max(img.width, img.height, 1)))
            if scale > 1:
                img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        out = []
        # canvas_size capa a resolução que o EasyOCR processa: telas grandes
        # (ex.: MetaTrader 1920px) ficam rápidas (<60s, senão o MCP reinicia o
        # server). As coordenadas voltam no espaço da imagem original.
        for bbox, txt, conf in self._ocr_reader.readtext(
                np.array(img), detail=1, paragraph=False, text_threshold=0.4,
                low_text=0.3, canvas_size=self._ocr_canvas, mag_ratio=1.0):
            if conf < min_conf or not txt.strip():
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2 / scale) + ox
            cy = int((bbox[0][1] + bbox[2][1]) / 2 / scale) + oy
            out.append({"text": txt.strip(), "x": cx, "y": cy, "confidence": round(float(conf), 2)})
        return out

    def find_text(self, image_path: str, text: str,
                  region: Optional[dict] = None,
                  *, min_conf: float = 0.3) -> Optional[dict]:
        """Find text position via OCR. Returns {x, y, text, confidence} or None.

        Upscales the search area before OCR so small/single-glyph controls
        (digits, short labels) are legible — EasyOCR is blind to them at native
        size in a busy/small window. Prefers an exact token match over substring
        (so '7' does not match '127'), then ranks by confidence.
        """
        if not _HAS_EASYOCR:
            return None

        self._ensure_ocr()
        img = Image.open(image_path).convert("RGB")
        ox, oy = 0, 0
        if region:
            ox = region.get("x", 0)
            oy = region.get("y", 0)
            w = region.get("width", img.width - ox)
            h = region.get("height", img.height - oy)
            img = img.crop((ox, oy, ox + w, oy + h))

        # Upscale so the smallest readable glyph clears EasyOCR's floor.
        scale = 1
        if max(img.width, img.height) < 1100:
            scale = max(2, min(4, 1100 // max(img.width, img.height, 1)))
            if scale > 1:
                img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)

        import numpy as _np
        results = self._ocr_reader.readtext(
            _np.array(img), detail=1, paragraph=False,
            text_threshold=0.4, low_text=0.3, canvas_size=self._ocr_canvas, mag_ratio=1.0,
        )

        text_lower = text.strip().lower()
        exact, partial = None, None
        for (bbox, txt, conf) in results:
            if conf < min_conf:
                continue
            t = txt.strip().lower()
            cx = int((bbox[0][0] + bbox[2][0]) / 2 / scale) + ox
            cy = int((bbox[0][1] + bbox[2][1]) / 2 / scale) + oy
            hit = {"x": cx, "y": cy, "text": txt.strip(), "confidence": float(conf)}
            if t == text_lower:
                if exact is None or conf > exact["confidence"]:
                    exact = hit
            elif text_lower in t:
                if partial is None or conf > partial["confidence"]:
                    partial = hit
        return exact or partial

    def region_changed(self, before_path: str, after_path: str,
                       region: Optional[dict] = None, *, thresh: float = 0.0015) -> bool:
        """True if a meaningful fraction of pixels changed inside `region`.

        Used as a real post-condition: a click that did nothing leaves the stage
        unchanged. Ignores tiny noise (cursor blink) via the threshold.
        """
        try:
            import numpy as np
            a = Image.open(before_path).convert("L")
            b = Image.open(after_path).convert("L")
            if region:
                x = region.get("x", 0); y = region.get("y", 0)
                w = region.get("width", a.width - x); h = region.get("height", a.height - y)
                box = (x, y, x + w, y + h)
                a = a.crop(box); b = b.crop(box)
            if a.size != b.size:
                return True
            aa = np.asarray(a, dtype=np.int16); bb = np.asarray(b, dtype=np.int16)
            diff = np.abs(aa - bb) > 25
            return float(diff.mean()) > thresh
        except Exception as e:
            print(f"[rpa] region_changed falhou: {e}", file=sys.stderr)
            return True  # fail-open: don't block on a verify error

    def ground(self, vlm, image_path: str, description: str,
               region: Optional[dict] = None) -> Optional[dict]:
        """Locate an element by natural-language description via a VLM.

        Fallback for glyph/icon controls that OCR can't read. Returns absolute
        {x, y} (screen coords) or None. The VLM answers in cropped-image pixels;
        we scale back and add the region offset.
        """
        import re
        import numpy as np
        img = Image.open(image_path).convert("RGB")
        ox, oy = 0, 0
        if region:
            ox = region.get("x", 0); oy = region.get("y", 0)
            w = region.get("width", img.width - ox); h = region.get("height", img.height - oy)
            img = img.crop((ox, oy, ox + w, oy + h))
        scale = 2 if max(img.width, img.height) < 700 else 1
        if scale > 1:
            img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        tmp = tempfile.mktemp(suffix=".png")
        img.save(tmp)
        prompt = (
            f"Esta imagem é uma interface gráfica de {img.width}x{img.height} pixels. "
            f"Devolva APENAS o centro do elemento '{description}' como JSON "
            f'{{"x": <int>, "y": <int>}} em pixels da imagem. Sem texto extra.'
        )
        try:
            ans = vlm.query(tmp, prompt)
        except Exception as e:
            print(f"[rpa] ground/vlm falhou: {e}", file=sys.stderr)
            ans = ""
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        nums = re.findall(r"-?\d+", ans or "")
        if len(nums) < 2:
            return None
        px, py = int(nums[0]), int(nums[1])
        ax = int(px / scale) + ox
        ay = int(py / scale) + oy
        return {"x": ax, "y": ay, "raw": (ans or "").strip()[:80]}

    # ── Diff ───────────────────────────────────────────────

    def diff(self, before_path: str, after_path: str) -> str:
        """Compare two screenshots and describe what changed."""
        before = cv2.imread(before_path)
        after = cv2.imread(after_path)

        if before is None or after is None:
            raise ValueError("Não foi possível ler as imagens")

        if before.shape != after.shape:
            return (f"Resolução alterada: {before.shape[1]}x{before.shape[0]} → "
                    f"{after.shape[1]}x{after.shape[0]}")

        # Absolute difference
        diff = cv2.absdiff(before, after)
        gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)

        # Find contours of changed regions
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return "Nenhuma mudança visual detectada."

        changes = []
        significant_contours = [c for c in contours if cv2.contourArea(c) > 100]
        for i, c in enumerate(significant_contours[:10]):
            x, y, w, h = cv2.boundingRect(c)
            changes.append(f"Região {i+1}: ({x},{y}) {w}x{h} — {cv2.contourArea(c):.0f}px²")

        total_pixels = np.sum(thresh == 255)
        pct = 100 * total_pixels / (thresh.shape[0] * thresh.shape[1])
        summary = f"{len(significant_contours)} regiões alteradas ({pct:.1f}% da tela)"
        if changes:
            summary += "\n" + "\n".join(changes)
        return summary

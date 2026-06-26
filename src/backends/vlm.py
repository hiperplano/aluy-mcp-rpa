"""VLM backend — visual language model fallback for hard cases."""

import os
import sys
import base64
import io
import urllib.request
import json
from typing import Optional

_HAS_PIL = False
try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    pass


class VlmBackend:
    """VLM queries — Ollama or BLIP (local)."""

    def __init__(self):
        if not _HAS_PIL:
            raise RuntimeError("Pillow não instalado")

        # Detect available models
        self._ollama_model = self._detect_ollama()
        self._blip_available = self._check_blip()

        if not self._ollama_model and not self._blip_available:
            raise RuntimeError(
                "Nenhum VLM disponível. Inicie Ollama com llava, minicpm-v ou cogvlm."
            )

        print(f"[rpa] VLM backend: ollama={self._ollama_model or 'não'}, blip={'sim' if self._blip_available else 'não'}",
              file=sys.stderr)

    def _detect_ollama(self) -> Optional[str]:
        """Check if Ollama is running and has a vision model."""
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                vision_models = [m for m in models
                                 if any(v in m.lower()
                                        for v in ["llava", "minicpm-v", "cogvlm",
                                                   "bakllava", "llama3.2-vision"])]
                return vision_models[0] if vision_models else None
        except Exception:
            return None

    def _check_blip(self) -> bool:
        """Check if BLIP transformers are available."""
        try:
            from transformers import BlipProcessor, BlipForConditionalGeneration
            return True
        except ImportError:
            return False

    def query(self, image_path: str, prompt: str,
              model: Optional[str] = None) -> str:
        """Query VLM about an image. Falls back from Ollama → BLIP."""
        if self._ollama_model:
            try:
                return self._query_ollama(image_path, prompt, model or self._ollama_model)
            except Exception as e:
                print(f"[rpa] Ollama falhou: {e}", file=sys.stderr)

        if self._blip_available:
            try:
                return self._query_blip(image_path, prompt)
            except Exception as e:
                print(f"[rpa] BLIP falhou: {e}", file=sys.stderr)

        return "Erro: nenhum VLM disponível para responder."

    def _query_ollama(self, image_path: str, prompt: str, model: str) -> str:
        img = Image.open(image_path)

        # Resize if too large
        max_dim = 672
        if img.width > max_dim or img.height > max_dim:
            ratio = max_dim / max(img.width, img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data.get("response", "(sem resposta)")

    def _query_blip(self, image_path: str, prompt: str) -> str:
        from transformers import BlipProcessor, BlipForConditionalGeneration

        processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
        model = BlipForConditionalGeneration.from_pretrained(
            "Salesforce/blip-image-captioning-base"
        )

        img = Image.open(image_path).convert("RGB")

        # BLIP is captioning-only, not VQA. Use a short prefix.
        short_prefix = "screenshot of"
        inputs = processor(img, short_prefix, return_tensors="pt")
        out = model.generate(**inputs, max_new_tokens=128)
        caption = processor.decode(out[0], skip_special_tokens=True)
        if caption.startswith(short_prefix):
            caption = caption[len(short_prefix):].strip().lstrip("-").strip()

        return (
            f"[BLIP captioning] {caption}\n\n"
            "Nota: modelo BLIP faz captioning, não VQA. "
            "Para perguntas específicas, use Ollama (ex: llava, minicpm-v)."
        )

    def describe(self, image_path: str) -> str:
        """Describe the screen in structured form."""
        prompt = (
            "Descreva esta captura de tela de forma estruturada:\n"
            "1. Qual aplicação/programa está visível?\n"
            "2. Liste os textos visíveis.\n"
            "3. Liste os elementos interativos (botões, campos, links).\n"
            "4. Qual o estado geral (normal, erro, carregando, sucesso)?\n"
            "Responda em português."
        )
        return self.query(image_path, prompt)

    def ask(self, image_path: str, question: str) -> str:
        """Ask a specific question about the screen."""
        return self.query(image_path, question)

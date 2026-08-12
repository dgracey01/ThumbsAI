"""
caption_backend.py — JoyCaption captioner for ThumbsAI (the AI-tagging engine).

Dual-entry, one code path (see the AI console):
  • INTERNAL — ThumbsAI launches its OWN llama-server with the JoyCaption gguf + mmproj (works with
               the Suite closed). Uses the transplanted llama_server.LlamaServer.
  • JARVIS   — reuse the Suite's already-running caption server (no second model in VRAM); ThumbsAI
               only POSTs images to it.
Either way an image is captioned via the identical OpenAI vision API (base64 image_url), and the
caption text becomes searchable `tags` (ai_ops.caption_to_tags writes them). stdlib + Pillow only.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from io import BytesIO

from llama_server import LlamaServer

_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve(p: str) -> str:
    """Resolve a configured path against the ThumbsAI folder when it's relative — so the app's own
    model copy (models/joycaption/…) travels with it, independent of any external model store."""
    if not p:
        return p
    return p if os.path.isabs(p) else os.path.join(_APP_DIR, p)

# Tagging instruction (not the SD-prompt style): a flat, comma-separated keyword list is what makes
# the FTS `tags` column searchable — "park, bench, red hair, denim jacket, overcast, candid".
TAG_INSTRUCTION = (
    "Tag this image in DANBOORU (booru) style: a comma-separated list of concise lowercase tags with "
    "underscores instead of spaces — e.g. 1girl, solo, long_hair, red_hair, blue_eyes, large_breasts, "
    "denim_jacket, outdoors, park, night, standing, looking_at_viewer, photorealistic. Order roughly: "
    "subject count first, then appearance, clothing, setting, action/pose, then art style. Prefer "
    "established booru tags. Output ONLY the tags separated by commas — lowercase, "
    "underscores_between_words, no sentences, no articles, no explanations.")


def _image_to_b64_jpeg(path: str, max_side: int = 1024) -> str:
    """Load ANY supported format (incl. avif/webp via Pillow), downscale, and JPEG-encode to base64 —
    normalises the input so llama.cpp's image decoder always accepts it (and keeps transfers small;
    JoyCaption's vision tower only sees 384px anyway)."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = BytesIO()
        im.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class Captioner:
    """Owns (INTERNAL) or points at (JARVIS) a JoyCaption llama-server and captions images."""

    def __init__(self, settings):
        self._s = settings
        self._srv: LlamaServer | None = None      # only used in INTERNAL mode

    # ── config helpers ────────────────────────────────────────────────────────────
    def mode(self) -> str:
        return (self._s.get("ai_mode", "internal") or "internal").lower()

    def _endpoint(self) -> str:
        if self.mode() == "jarvis":
            return (self._s.get("ai_jarvis_caption_url") or "http://127.0.0.1:8081").rstrip("/")
        return f"http://127.0.0.1:{int(self._s.get('ai_internal_port', 8082))}"

    # ── lifecycle ─────────────────────────────────────────────────────────────────
    def jarvis_server_up(self) -> bool:
        """Is the Suite's JoyCaption already resident? Used to warn before loading a SECOND copy into
        VRAM (two JoyCaptions ≈ 12 GB — the whole card on a 16 GB GPU)."""
        return _health(self._s.get("ai_jarvis_caption_url") or "http://127.0.0.1:8081")

    def ensure_up(self) -> dict:
        """Make a captioning endpoint available. INTERNAL launches our own server (idempotent); JARVIS
        only checks the Suite's server is reachable (we never start another process's model); OFF refuses
        outright so nothing is loaded."""
        if self.mode() == "off":
            return {"ok": False, "error": "AI is switched OFF (AI console ▸ mode). "
                                          "Choose Internal or Jarvis to caption. (Find dupes needs no model.)"}
        if self.mode() == "jarvis":
            ok = _health(self._endpoint())
            return {"ok": ok} if ok else {
                "ok": False, "error": "Jarvis caption server not reachable — start Jarvis (and load "
                "JoyCaption) or switch the AI console to Internal mode."}
        gguf = _resolve(self._s.get("ai_joycaption_gguf", ""))
        mmproj = _resolve(self._s.get("ai_joycaption_mmproj", ""))
        if not gguf or not os.path.isfile(gguf):
            return {"ok": False, "error": f"JoyCaption model not found: {gguf}"}
        if not mmproj or not os.path.isfile(mmproj):
            return {"ok": False, "error": f"JoyCaption mmproj not found: {mmproj}"}
        if self._srv is None:
            log = os.path.join(_APP_DIR, "data", ".joycaption_server.log")
            self._srv = LlamaServer(_resolve(self._s.get("ai_llama_dir", "")), port=int(self._s.get("ai_internal_port", 8082)),
                                    log_path=log)
        if self._srv.is_running() and self._srv.is_ready():
            return {"ok": True, "already": True}
        preset = {"n_ctx": int(self._s.get("ai_n_ctx", 4096)),
                  "n_gpu_layers": int(self._s.get("ai_n_gpu_layers", -1)),
                  "flash_attn": bool(self._s.get("ai_flash_attn", True)),
                  "cache_type_k": self._s.get("ai_cache_type_k", "f16"),
                  "cache_type_v": self._s.get("ai_cache_type_v", "f16"),
                  "mmproj": mmproj}
        # Pin JoyCaption to a chosen Vulkan GPU (default the RX 580) so it never touches the render GPU.
        # Use the AUTHORITATIVE `--device Vulkan<N>` flag (Vulkan0 = RX 7800 XT, Vulkan1 = RX 580) plus
        # `--split-mode none` (all on that one card). GGML_VK_VISIBLE_DEVICES is flaky on this Vulkan build
        # and was landing the model on the wrong GPU; --device is honored. Verify in Task Manager ▸ GPU.
        dev = str(self._s.get("ai_vulkan_device", "")).strip()
        if dev != "":
            preset = {**preset, "extra_args": [*preset.get("extra_args", []),
                                               "--device", f"Vulkan{dev}", "--split-mode", "none"]}
        return self._srv.start(gguf, preset, wait=200.0)

    def caption(self, image_path: str, instruction: str | None = None) -> str:
        """Return the caption/tags for one image, or a string starting with 'ERROR'/'CAPTION FAILED'.
        Persona + sampling come from settings (Settings ▸ AI), falling back to the built-in defaults."""
        if not os.path.isfile(image_path):
            return f"ERROR: not found: {image_path}"
        instruction = instruction or self._s.get("ai_instruction") or TAG_INSTRUCTION
        try:
            b64 = _image_to_b64_jpeg(image_path)
        except Exception as e:
            return f"ERROR reading image: {type(e).__name__}: {e}"
        payload = {"model": "joycaption",
                   "temperature": float(self._s.get("ai_temperature", 0.4)),
                   "max_tokens": int(self._s.get("ai_max_tokens", 200)), "stream": False,
                   "messages": [{"role": "user", "content": [
                       {"type": "text", "text": instruction},
                       {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]}
        try:
            req = urllib.request.Request(self._endpoint() + "/v1/chat/completions",
                                         data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"CAPTION FAILED: {type(e).__name__}: {e}"

    def stop(self):
        """Stop our own server (INTERNAL). Never touches Jarvis's process."""
        if self._srv is not None:
            self._srv.stop()


def _health(base: str) -> bool:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/health", timeout=2.0) as r:
            return r.status == 200
    except Exception:
        return False

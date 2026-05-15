"""
ai_metadata.py — AI generation metadata parser for ThumbsAI
Designed by: Zero  |  Built by: Jarvis

Supports: Automatic1111 WebUI, ComfyUI, NovelAI, InvokeAI
Reads metadata embedded in PNG tEXt chunks — no internet required.
"""
from __future__ import annotations
import json
import re


def parse_png_metadata(filepath: str | None, raw_bytes: bytes | None = None) -> dict:
    """
    Extract AI generation metadata from a PNG file or raw bytes.
    Returns a dict with keys: prompt, negative_prompt, seed, model,
    sampler, cfg_scale, steps, source, raw_meta.
    Returns {} if no AI metadata found.
    """
    try:
        from PIL import Image
        from io import BytesIO
        if raw_bytes is not None:
            with Image.open(BytesIO(raw_bytes)) as img:
                info = dict(img.info or {})
        else:
            with Image.open(filepath) as img:
                info = dict(img.info or {})
    except Exception:
        return {}

    # A1111 / AUTOMATIC1111
    if "parameters" in info:
        return _parse_a1111(info["parameters"])

    # ComfyUI — "prompt" key contains JSON node graph
    if "prompt" in info:
        try:
            data = json.loads(info["prompt"])
            if isinstance(data, dict):
                return _parse_comfyui(data)
        except Exception:
            pass

    # NovelAI
    if "Description" in info or "Comment" in info:
        return _parse_novelai(info)

    # InvokeAI
    if "sd-metadata" in info:
        return _parse_invokeai(info["sd-metadata"])

    return {}


def _parse_a1111(params: str) -> dict:
    """Parse A1111 parameters string."""
    result: dict = {"source": "A1111", "raw_meta": params}
    lines = params.strip().split("\n")

    # Locate "Negative prompt:" and "Steps:" lines
    neg_idx = next(
        (i for i, l in enumerate(lines)
         if l.strip().lower().startswith("negative prompt:")), None)
    set_idx = next(
        (i for i, l in enumerate(lines)
         if re.match(r'^\s*Steps\s*:', l, re.IGNORECASE)), None)

    if neg_idx is not None:
        result["prompt"] = "\n".join(lines[:neg_idx]).strip()
        end = set_idx if (set_idx is not None and set_idx > neg_idx) else len(lines)
        neg_text = "\n".join(lines[neg_idx:end])
        result["negative_prompt"] = re.sub(
            r'^Negative prompt:\s*', '', neg_text, flags=re.IGNORECASE).strip()
    elif set_idx is not None:
        result["prompt"] = "\n".join(lines[:set_idx]).strip()
    else:
        result["prompt"] = params.strip()

    settings_str = " ".join(lines[set_idx:]) if set_idx is not None else ""

    def _val(key: str) -> str:
        m = re.search(rf'{re.escape(key)}\s*:\s*([^,\n]+)', settings_str, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    result["steps"]     = _val("Steps")
    result["sampler"]   = _val("Sampler")
    result["cfg_scale"] = _val("CFG scale")
    result["seed"]      = _val("Seed")
    result["model"]     = _val("Model") or _val("Model hash")

    return {k: v for k, v in result.items() if v or k in ("source", "raw_meta")}


def _parse_comfyui(prompt_json: dict) -> dict:
    """Parse ComfyUI prompt JSON node graph."""
    result: dict = {
        "source":   "ComfyUI",
        "raw_meta": json.dumps(prompt_json, indent=2),
    }
    prompts: list[str] = []

    for node in prompt_json.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        cls    = node.get("class_type", "")

        if cls == "CLIPTextEncode":
            text = inputs.get("text", "")
            if isinstance(text, str) and text.strip():
                prompts.append(text.strip())

        if cls in ("KSampler", "KSamplerAdvanced"):
            if "seed"         in inputs: result.setdefault("seed",      str(inputs["seed"]))
            if "steps"        in inputs: result.setdefault("steps",     str(inputs["steps"]))
            if "sampler_name" in inputs: result.setdefault("sampler",   inputs["sampler_name"])
            if "cfg"          in inputs: result.setdefault("cfg_scale", str(inputs["cfg"]))

        if cls in ("CheckpointLoaderSimple", "CheckpointLoader"):
            result.setdefault("model", inputs.get("ckpt_name", ""))

    if prompts:
        result["prompt"] = prompts[0]
        if len(prompts) > 1:
            result["negative_prompt"] = prompts[1]

    return {k: v for k, v in result.items() if v or k in ("source", "raw_meta")}


def _parse_novelai(info: dict) -> dict:
    """Parse NovelAI PNG metadata."""
    result: dict = {
        "source":   "NovelAI",
        "raw_meta": str(info),
        "prompt":   info.get("Description", ""),
    }
    try:
        data = json.loads(info.get("Comment", "{}"))
        result["seed"]      = str(data.get("seed", ""))
        result["sampler"]   = data.get("sampler", "")
        result["steps"]     = str(data.get("steps", ""))
        result["cfg_scale"] = str(data.get("scale", ""))
        result["model"]     = data.get("model", "")
    except Exception:
        pass
    return {k: v for k, v in result.items() if v or k in ("source", "raw_meta")}


def _parse_invokeai(raw: str) -> dict:
    """Parse InvokeAI sd-metadata."""
    result: dict = {"source": "InvokeAI", "raw_meta": raw}
    try:
        data = json.loads(raw)
        img  = data.get("image", data)
        result["prompt"]    = img.get("prompt", [{}])[0].get("prompt", "") if isinstance(img.get("prompt"), list) else img.get("prompt", "")
        result["seed"]      = str(img.get("seed", ""))
        result["steps"]     = str(img.get("steps", ""))
        result["cfg_scale"] = str(img.get("cfg_scale", ""))
        result["sampler"]   = img.get("sampler", "")
        result["model"]     = data.get("model_weights", "")
    except Exception:
        pass
    return {k: v for k, v in result.items() if v or k in ("source", "raw_meta")}


# ── Metadata write-back ───────────────────────────────────────────────────────

def build_a1111_params(fields: dict) -> str:
    """
    Reconstruct an A1111-style 'parameters' string from a field dict.
    This is the format CivitAI, A1111, and most SD tools expect in PNG tEXt chunks.

      {prompt}
      Negative prompt: {negative_prompt}
      Steps: N, Sampler: X, CFG scale: Y, Seed: Z, Model: M
    """
    parts: list[str] = []
    if fields.get("prompt"):
        parts.append(fields["prompt"].strip())
    if fields.get("negative_prompt"):
        parts.append(f"Negative prompt: {fields['negative_prompt'].strip()}")
    settings: list[str] = []
    for key, label in (("steps",     "Steps"),
                        ("sampler",   "Sampler"),
                        ("cfg_scale", "CFG scale"),
                        ("seed",      "Seed"),
                        ("model",     "Model")):
        if fields.get(key):
            settings.append(f"{label}: {fields[key]}")
    if settings:
        parts.append(", ".join(settings))
    return "\n".join(parts)


# tEXt keys written by each generator — we replace all of them on write-back
# so the file stays consistent and doesn't carry stale chunks.
_SOURCE_CHUNK_KEYS = {"parameters", "prompt", "Comment", "Description", "sd-metadata"}


def write_metadata_to_file(filepath: str, fields: dict) -> str | None:
    """
    Write AI generation metadata back into the image file on disk.

    PNG  — rewrites the 'parameters' tEXt chunk (A1111 format).
           All non-AI text chunks (e.g. software, creation_time) are preserved.
           Pixel data and ICC profile are untouched.
           Write is atomic: temp file → os.replace.

    JPEG / WebP / other — returns a warning string; metadata is DB-only for
           these formats because reliable cross-tool injection requires piexif
           which is not currently installed.

    Returns None on success, or a non-fatal warning/error string.
    """
    from pathlib import Path
    ext = Path(filepath).suffix.lower()

    if ext != ".png":
        fmt = ext.lstrip(".").upper() or "this format"
        return (f"{fmt} files do not support PNG-style text chunks — "
                f"metadata saved to database only.")

    try:
        import os, tempfile
        from PIL import Image, PngImagePlugin

        with Image.open(filepath) as img:
            img.load()                        # force full decode before closing file handle
            existing  = dict(img.info or {})
            icc       = existing.get("icc_profile")

            pnginfo = PngImagePlugin.PngInfo()

            # Preserve non-AI text chunks verbatim
            for k, v in existing.items():
                if isinstance(v, str) and k not in _SOURCE_CHUNK_KEYS:
                    pnginfo.add_text(k, v)

            # Write updated AI parameters in A1111 format
            params = build_a1111_params(fields)
            if params:
                pnginfo.add_text("parameters", params)

            save_kwargs: dict = {"format": "PNG", "pnginfo": pnginfo}
            if icc:
                save_kwargs["icc_profile"] = icc

            # Atomic write: temp file in same directory → rename
            dirpath  = os.path.dirname(os.path.abspath(filepath))
            fd, tmp  = tempfile.mkstemp(dir=dirpath, suffix=".tmp.png")
            os.close(fd)
            try:
                img.save(tmp, **save_kwargs)
                os.replace(tmp, filepath)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

        return None

    except Exception as exc:
        return f"File write failed: {exc}"

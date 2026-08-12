#!/usr/bin/env python
"""
llama_server.py - manage a local llama-server.exe (AMD ROCm build) for Jarvis Phase 4.

Jarvis's own in-process brain host, replacing the LM Studio server. We launch AMD's prebuilt
ROCm-7.2.1 llama-server.exe (OpenAI-compatible) with a model's PER-MODEL flag preset, wait for it
to become healthy, and stop it on demand (VRAM arbiter, model switch, shutdown).

The GPU/CPU tuning the user set in LM Studio is carried here 1:1 via a preset dict (see
jarvis_model_presets.py). llama-server speaks the same /v1/chat/completions the LM Studio client
already used, so the chat path barely changes.

HARDWARE NOTE (see llama_cpp/README_ZEN2_CPU_FIX.txt): AMD's bundle ships an AVX-512 ggml-cpu.dll
that SIGILLs on this Ryzen 9 3950X (Zen 2). The bundle's ggml-cpu.dll has been replaced with the
upstream b8407 'haswell' variant. This module just launches the exe; the fix lives in the bundle.

stdlib only.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time
import urllib.request

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _llama_server_procs() -> list:
    """[(pid, commandline)] of running llama-server.exe (Windows CIM). Empty on failure/non-Windows."""
    if sys.platform != "win32":
        return []
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=15, creationflags=_NO_WINDOW).stdout or ""
    except Exception:
        return []
    procs = []
    for line in out.splitlines():
        if "\t" in line:
            pid_s, cmd = line.split("\t", 1)
            try:
                procs.append((int(pid_s.strip()), cmd))
            except ValueError:
                pass
    return procs


def kill_stale_servers(ports) -> int:
    """taskkill any llama-server.exe bound to one of `ports` — our own JoyCaption server orphaned (and
    squatting on VRAM) after a crashed/closed ThumbsAI session. Only touches OUR port(s), so a co-running
    Jarvis captioner (8081) is never harmed. Returns how many were killed."""
    if sys.platform != "win32":
        return 0
    import re
    want = {int(p) for p in ports}
    killed = 0
    for pid, cmd in _llama_server_procs():
        m = re.search(r"--port (\d+)", cmd)
        if m and int(m.group(1)) in want:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                               creationflags=_NO_WINDOW, timeout=10)
                killed += 1
            except Exception:
                pass
    return killed


def find_server_exe(llama_dir: str) -> str | None:
    """Locate llama-server.exe under <llama_dir>/bin/<any-build>/ so a future b-tag update still
    resolves without editing config. Returns the newest match or None."""
    pats = [
        os.path.join(llama_dir, "bin", "*", "llama-server.exe"),
        os.path.join(llama_dir, "bin", "llama-server.exe"),
        os.path.join(llama_dir, "llama-server.exe"),
    ]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    if not hits:
        return None
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0]


def preset_to_args(preset: dict) -> list[str]:
    """Map a per-model preset (jarvis_model_presets schema) to llama-server CLI args.

    Recognized keys (all optional; omitted => llama-server default):
      n_ctx, n_gpu_layers(-1/999=all), flash_attn(bool), cache_type_k, cache_type_v,
      n_batch, n_ubatch, n_threads, n_cpu_moe(int>0), seed(>=0), mmproj(path), extra_args(list).
    """
    a: list[str] = []
    p = preset or {}

    def has(k):
        return p.get(k) is not None

    if has("n_ctx"):        a += ["-c", str(int(p["n_ctx"]))]
    ngl = p.get("n_gpu_layers", 999)
    a += ["-ngl", "999" if ngl in (-1, None) else str(int(ngl))]
    if has("flash_attn"):   a += ["-fa", "on" if p["flash_attn"] else "off"]
    if p.get("cache_type_k"): a += ["--cache-type-k", str(p["cache_type_k"])]
    if p.get("cache_type_v"): a += ["--cache-type-v", str(p["cache_type_v"])]
    if has("n_batch"):      a += ["-b", str(int(p["n_batch"]))]
    if has("n_ubatch"):     a += ["-ub", str(int(p["n_ubatch"]))]
    if has("n_threads"):    a += ["-t", str(int(p["n_threads"]))]
    if p.get("n_cpu_moe"):  a += ["--n-cpu-moe", str(int(p["n_cpu_moe"]))]
    if has("seed") and int(p["seed"]) >= 0:
        a += ["--seed", str(int(p["seed"]))]
    if p.get("mmproj"):     a += ["--mmproj", str(p["mmproj"])]
    a += list(p.get("extra_args", []) or [])
    return a


class LlamaServer:
    """One llama-server.exe process. Reused across model switches (stop + start with new args)."""

    def __init__(self, llama_dir: str, host: str = "127.0.0.1", port: int = 1234,
                 log_path: str | None = None):
        self.llama_dir = llama_dir
        self.exe = find_server_exe(llama_dir)
        self.host = host
        self.port = int(port)
        self.proc: subprocess.Popen | None = None
        self.model_path: str | None = None
        self._log_path = log_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  ".llama_server.log")

    # ── url helpers ─────────────────────────────────────────────────────────────
    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def is_ready(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=timeout) as r:
                return b'"ok"' in r.read() or r.status == 200
        except Exception:
            return False

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ── lifecycle ───────────────────────────────────────────────────────────────
    def start(self, model_path: str, preset: dict, wait: float = 240.0, env: dict | None = None) -> dict:
        """Launch the server for `model_path` with `preset` flags; block until healthy or `wait`s.
        If a server is already running for a DIFFERENT model, it is stopped first.
        `env` overrides are merged into the child's environment — e.g. GGML_VK_VISIBLE_DEVICES to pin
        the Vulkan GPU (JoyCaption on the RX 580 while the 7800 XT stays free for SDXL)."""
        if not self.exe or not os.path.exists(self.exe):
            return {"ok": False, "error": f"llama-server.exe not found under {self.llama_dir}"}
        if not os.path.exists(model_path):
            return {"ok": False, "error": f"model not found: {model_path}"}
        # already serving this exact model+port? reuse it.
        if self.is_running() and self.model_path == model_path and self.is_ready():
            return {"ok": True, "already": True, "pid": self.proc.pid}
        self.stop()
        # --jinja: use the model's chat template (needed for tool-calls). --reasoning-format none:
        # return the FULL text in message.content (incl. any <think>) instead of splitting it into
        # reasoning_content (which left content empty) — Jarvis does its own thinking display, and this
        # matches what the LM Studio client returned. --parallel 1: single sequence (Jarvis is 1 user).
        args = [self.exe, "-m", model_path, "--host", self.host, "--port", str(self.port),
                "--no-webui", "--parallel", "1", "--jinja", "--reasoning-format", "none"
                ] + preset_to_args(preset)
        exe_dir = os.path.dirname(self.exe)   # cwd = bundle dir so ROCm/Tensile DLLs + kernels resolve
        proc_env = None
        if env:
            proc_env = dict(os.environ)
            proc_env.update({k: str(v) for k, v in env.items() if v is not None and str(v) != ""})
        try:
            logf = open(self._log_path, "w", encoding="utf-8", errors="replace")
            self.proc = subprocess.Popen(args, cwd=exe_dir, stdout=logf, stderr=subprocess.STDOUT,
                                         creationflags=_NO_WINDOW, env=proc_env)
        except Exception as e:
            return {"ok": False, "error": f"launch failed: {type(e).__name__}: {e}"}
        self.model_path = model_path
        t0 = time.time()
        while time.time() - t0 < wait:
            if not self.is_running():
                return {"ok": False, "error": "server exited during load", "log_tail": self.log_tail(),
                        "args": args}
            if self.is_ready():
                return {"ok": True, "pid": self.proc.pid, "secs": round(time.time() - t0, 1),
                        "base": self.base}
            time.sleep(1.0)
        self.stop()
        return {"ok": False, "error": f"not healthy within {wait}s", "log_tail": self.log_tail()}

    def stop(self) -> bool:
        """Terminate the server (graceful, then forced). Frees its VRAM."""
        p = self.proc
        self.proc = None
        self.model_path = None
        if p is None:
            return False
        try:
            p.terminate()
            try:
                p.wait(timeout=8)
            except Exception:
                p.kill()
        except Exception:
            pass
        # belt-and-braces: kill any stragglers on our port's exe (Windows sometimes leaves a child)
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True,
                           creationflags=_NO_WINDOW, timeout=10)
        except Exception:
            pass
        return True

    def log_tail(self, n: int = 25) -> str:
        try:
            with open(self._log_path, encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except Exception:
            return ""


if __name__ == "__main__":
    import json
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "llama_cpp", "vulkan_b10012")
    print("server exe:", find_server_exe(d))
    if len(sys.argv) > 2:
        srv = LlamaServer(d, port=1236)
        preset = {"n_ctx": 2048, "n_gpu_layers": 999, "flash_attn": True}
        print(json.dumps(srv.start(sys.argv[2], preset), indent=2, default=str))
        try:
            print("ready?", srv.is_ready())
        finally:
            srv.stop()
            print("stopped")

"""离线语音（P1 可选）：sherpa-onnx 本地中文 TTS，无网络依赖。

依赖与模型都是可选的（不装不影响主功能）：
  pip install sherpa-onnx           # wheel ~4MB
  下载模型到 $AGENT_HOME/tts-model   # 见 termux/voice.md

sherpa-onnx 不可用时工具返回安装指引，不抛异常。
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

from . import Tool, register

_MODEL_DIR_NAMES = ("tts-model", "sherpa-onnx-vits-zh-ll", "sherpa-onnx-vits-zh")


def _home() -> Path:
    return Path(os.environ.get("AGENT_HOME") or Path.home() / ".agent" / "termux-agent")


def _find_model_dir(home: Path) -> Path | None:
    for name in _MODEL_DIR_NAMES:
        p = home / name
        if (p / "model.onnx").exists() and (p / "tokens.txt").exists():
            return p
    return None


def voice_status() -> dict:
    """供设置面板/工具使用的能力探测。"""
    if importlib.util.find_spec("sherpa_onnx") is None:
        return {"available": False, "reason": "未安装 sherpa-onnx（pip install sherpa-onnx，可选）"}
    d = _find_model_dir(_home())
    if d is None:
        return {"available": False, "reason": f"未找到离线模型（{_home()}/tts-model），见 termux/voice.md"}
    return {"available": True, "model_dir": str(d), "engine": "sherpa-onnx vits-zh"}


_engine: dict = {"tts": None, "model_dir": ""}


def _get_engine(model_dir: Path):
    if _engine["tts"] is not None and _engine["model_dir"] == str(model_dir):
        return _engine["tts"]
    import sherpa_onnx

    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model_dir / "model.onnx"),
                lexicon=str(model_dir / "lexicon.txt") if (model_dir / "lexicon.txt").exists() else "",
                dict_dir=str(model_dir / "dict") if (model_dir / "dict").is_dir() else "",
                tokens=str(model_dir / "tokens.txt"),
                data_dir=str(model_dir),
                noise_scale=0.6,
                length_scale=1.0,
            ),
            num_threads=2,
        ),
        rule_fsts=",".join(
            str(model_dir / f) for f in ("date.fst", "number.fst", "phone.fst") if (model_dir / f).exists()
        ),
        max_num_sentences=1,
    )
    _engine["tts"] = sherpa_onnx.OfflineTts(cfg)
    _engine["model_dir"] = str(model_dir)
    return _engine["tts"]


async def _tts_offline(text: str) -> dict:
    """本地合成中文语音（无网络）。返回 wav 路径。"""
    status = voice_status()
    if not status["available"]:
        return {"error": status["reason"]}
    if not text or not str(text).strip():
        return {"error": "请提供要朗读的文本"}
    text = str(text).strip()[:500]
    try:
        tts = _get_engine(Path(status["model_dir"]))
        audio = tts.generate(text, sid=0, speed=1.0)
        if not audio.samples:
            return {"error": "合成失败（模型不支持该文本或未初始化）"}
        cache = _home() / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        out = cache / f"tts_{int(time.time())}.wav"
        try:
            import wave

            import numpy as np

            samples = np.asarray(audio.samples, dtype=np.int16)
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(audio.sample_rate)
                w.writeframes(samples.tobytes())
        except Exception:  # noqa: BLE001 —— numpy 缺失时用 struct 回退
            import struct
            import wave

            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(audio.sample_rate)
                w.writeframes(struct.pack(f"<{len(audio.samples)}h", *[int(max(-1, min(1, s)) * 32767) for s in audio.samples]))
        return {
            "ok": True,
            "path": str(out),
            "duration_seconds": round(len(audio.samples) / audio.sample_rate, 1),
            "sample_rate": audio.sample_rate,
            "engine": "sherpa-onnx（离线）",
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"离线语音失败：{e}"}


def register_voice_tool() -> None:
    register(
        Tool(
            name="tts_offline",
            description="本地离线朗读文本（sherpa-onnx 中文 TTS，无需网络），返回生成的 wav 文件路径。",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要朗读的中文文本"},
                },
                "required": ["text"],
            },
            risk="safe",
            handler=_tts_offline,
            summary="本地离线语音朗读",
            group="voice",
            timeout=60,
        )
    )

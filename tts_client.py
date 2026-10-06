"""
tts_client.py — TTS (gRPC，Cyberon 協定) 封裝。

backend 由 config.TTS_BACKEND 選擇（cyberon / f5），各自的 host/port/speaker/
speed/gain/token 在 config.TTS_BACKENDS 分開設定。

流程：
  1. grpclib.async TTS → 取 16kHz WAV bytes
  2. wave + audioop.ratecv 降採樣為 8kHz 16-bit WAV
  3. 存到 TTS_AUDIO_DIR/{uuid8}_{turn}.wav，供 Asterisk Playback 使用

對外只暴露 synthesize(text, uuid, turn)；失敗回傳空字串。
"""
import asyncio
import audioop
import io
import os
import ssl
import sys
import time
import wave as _wave
from concurrent.futures import ThreadPoolExecutor

# 載入 service_pb2 / service_grpc（與本檔同目錄）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grpclib.client import Channel
import service_pb2 as pb
import service_grpc as pb_grpc

from config import TTS_BACKEND, TTS_BACKENDS, TTS_AUDIO_DIR

if TTS_BACKEND not in TTS_BACKENDS:
    raise ValueError(f"TTS_BACKEND={TTS_BACKEND!r} 不在 TTS_BACKENDS {list(TTS_BACKENDS)} 裡")
_cfg = TTS_BACKENDS[TTS_BACKEND]


# 同步介面用：在獨立執行緒跑 asyncio loop
_executor = ThreadPoolExecutor(max_workers=4)


def _make_tls_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_alpn_protocols(["h2"])
    return ctx


async def _tts_async(text: str) -> bytes:
    """呼叫目前選定的 TTS backend，回傳 16kHz WAV bytes。"""
    channel = Channel(_cfg["host"], _cfg["port"], ssl=_make_tls_ctx())
    try:
        stub = pb_grpc.StreamServiceStub(channel)
        print(f"🔧 TTS 送出：[{TTS_BACKEND}] {_cfg['host']}:{_cfg['port']} speaker={_cfg['speaker']} "
              f"speed={_cfg['speed']} gain={_cfg['gain']}", flush=True)
        req = pb.TtsRequest(
            serviceName="e2e",
            text=text,
            outfmt="wav",
            language="zh-TW",
            speaker=_cfg["speaker"],
            speed=_cfg["speed"],
            gain=_cfg["gain"],
            token=_cfg["token"],
        )
        chunks: list[bytes] = []
        async with stub.TTS.open() as stream:
            await stream.send_message(req, end=True)
            async for resp in stream:
                chunks.append(resp.data)
        return b"".join(chunks)
    finally:
        channel.close()


def synthesize(text: str, uuid: str, turn: int) -> str:
    """同步包裝：16kHz WAV → 8kHz 16-bit WAV 存檔。失敗回傳空字串。"""
    try:
        t0 = time.time()
        wav16k = _executor.submit(asyncio.run, _tts_async(text)).result(timeout=15)
        elapsed = time.time() - t0

        # 從 16kHz WAV 取 raw PCM，降採樣到 8kHz（Asterisk format_wav.c 要求）
        with _wave.open(io.BytesIO(wav16k)) as wf:
            pcm16k = wf.readframes(wf.getnframes())
        pcm8k, _ = audioop.ratecv(pcm16k, 2, 1, 16000, 8000, None)

        path = os.path.join(TTS_AUDIO_DIR, f"{uuid[:8]}_{turn}.wav")
        with _wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)   # 16-bit
            wf.setframerate(8000)
            wf.writeframes(pcm8k)
        print(f"🔊 [{uuid[:8]}] TTS 完成：{len(pcm8k)} bytes，{elapsed:.2f}s → {path}", flush=True)
        return path
    except Exception as e:
        print(f"⚠️  TTS 失敗 [{uuid[:8]}]：{e!r}", flush=True)
        return ""

"""Work around MLX's thread-local GPU stream limitation (mlx 0.31.2).

WhisperLiveKit runs transcription inference in a worker thread, but MLX GPU
streams are thread-local: calling mx.eval on a thread that has no stream raises
"There is no Stream(gpu, N) in current thread." The upstream fix (ml-explore/mlx
PR #3355/#3408, ThreadLocalStream) is not yet released, and the documented
"eval on the creating thread" workaround does NOT help on 0.31.2.

What works (verified empirically): wrap the inference call in
`with mx.stream(<a stream created on this thread>):`. We give each worker thread
its own GPU stream (cached via threading.local) and wrap MLXAlignAtt.infer so all
mx ops inside it — including the mx.eval calls in _evaluate — run on that stream.

This is a monkey-patch, not a WLK source edit, so it stays drop-in across WLK
upgrades and is a no-op for non-MLX backends.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("c3po")

_thread_streams = threading.local()


def _thread_gpu_stream():
    """Return a GPU stream owned by the current thread, creating it once."""
    import mlx.core as mx

    stream = getattr(_thread_streams, "stream", None)
    if stream is None:
        stream = mx.new_stream(mx.gpu)
        _thread_streams.stream = stream
    return stream


def apply() -> bool:
    """Patch MLXAlignAtt.infer to run on a per-thread GPU stream.

    Returns True if the patch was applied, False if MLX isn't in use / unavailable.
    Safe to call once at startup before any transcription runs.
    """
    try:
        import mlx.core as mx
        from whisperlivekit.simul_whisper.mlx import MLXAlignAtt
    except Exception:  # noqa: BLE001 - MLX not installed or backend not MLX
        return False

    if MLXAlignAtt is None or getattr(MLXAlignAtt, "_c3po_stream_patched", False):
        return False

    original_infer = MLXAlignAtt.infer

    def infer_on_thread_stream(self, *args, **kwargs):
        with mx.stream(_thread_gpu_stream()):
            return original_infer(self, *args, **kwargs)

    MLXAlignAtt.infer = infer_on_thread_stream
    MLXAlignAtt._c3po_stream_patched = True
    logger.info("Applied MLX per-thread GPU stream patch to MLXAlignAtt.infer")
    return True

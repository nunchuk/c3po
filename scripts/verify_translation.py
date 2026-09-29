"""Smoke test: NLLB EN->ZH translation via NLLW (mirrors WLK core.py usage).

Run:
    source .venv/bin/activate && python3 -u scripts/verify_translation.py
"""

from __future__ import annotations

import sys

from nllw import OnlineTranslation, TimedText, load_model


def main() -> int:
    print("loading NLLB 600M (en source)...", flush=True)
    model = load_model(["en"], nllb_backend="transformers", nllb_size="600M")
    print("model loaded", flush=True)

    ot = OnlineTranslation(model, ["en"], ["zho_Hans"])
    print("OnlineTranslation ready", flush=True)

    # ASR tokens carry leading spaces / word boundaries. Mimic that, and feed
    # two full sentences so LocalAgreement can finalize a prefix.
    words = [
        "Hello", " everyone", ".",
        " Can", " you", " hear", " me", " clearly", "?",
        " I", " think", " the", " latency", " looks", " good", ".",
    ]
    t = 0.0
    for w in words:
        ot.insert_tokens([TimedText(text=w, start=t, end=t + 0.3)])
        t += 0.3
        out = ot.process()
        validated, buffer = out if isinstance(out, tuple) else (out, None)
        if validated and str(validated).strip():
            print(f"  validated -> {str(validated)!r}", flush=True)

    final = ot.validate_buffer_and_reset()
    print(f"final buffer flush -> {final!r}", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

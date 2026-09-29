# CLAUDE.md — c3po

AI collaboration guidance for this repository. These instructions override default behavior.

## Project

c3po is a local meeting assistant for MS Teams / Zoom on Apple Silicon (M4 / 16GB). It does two things:

1. **Live transcription + EN→ZH translation** — WhisperLiveKit (`faster-whisper`, `base.en`) transcribes locally; **translation is done by c3po on Amazon Bedrock (Claude Haiku 4.5), per finalized English sentence**, NOT by WLK's built-in NLLW. Audio is captured from the desktop client through a **BlackHole** virtual audio device.
2. **English reply suggestions** — on **Amazon Bedrock (Claude Haiku 4.5)**, regenerated continuously in the background so a few fresh reply options always sit in the GUI.

Single FastAPI process. It embeds WhisperLiveKit's transcription pipeline and serves WLK's built-in UI with an injected reply panel. See `README.md` for setup and run instructions.

**Why Bedrock translation instead of WLK's NLLW:** NLLW couples translation to transcript segmentation by timestamp overlap, so finer line-splitting (needed for readability) broke its output (partial/fragmented Chinese). Translating each finalized English sentence independently on Bedrock decouples the two — segmentation and translation no longer fight, Chinese is complete and fluent, and NLLB's ~1.5GB is freed. Two monkey-patches (no WLK fork) make this work: `segment_fix.py` (split lines on sentence punctuation, since WLK only breaks on >5s silence when diarization is off) and `mlx_thread_fix.py` (only if `mlx-whisper` backend is selected).

## Working principles (from Andrej Karpathy's notes on common LLM coding mistakes)

Source: https://github.com/multica-ai/andrej-karpathy-skills (principles distilled here; the plugin is not installed).

1. **Think Before Coding** — don't assume, don't hide confusion, surface tradeoffs.
2. **Simplicity First** — the minimum code that solves the problem; nothing speculative.
3. **Surgical Changes** — touch only what you must; clean up only your own mess.
4. **Goal-Driven Execution** — define success criteria, loop until verified.

## Python conventions

- Use `python3` / `pip3` — never bare `pip` / `python`.
- Always work inside the virtualenv; activate it before installing. Pin versions in `requirements.txt`.
- `.venv/` and `.env` are gitignored. Never commit secrets; configuration comes from environment variables (`.env`).
- Type hints, PEP 8, docstrings on functions and classes. Use `black` / `flake8` when available.
- **All code comments, documentation, and CLI output are in English.** (Conversation with the user may be in Chinese; this rule is about the codebase only.)

## Doc & style discipline

- Do not create README/guide/summary files or add emojis unless explicitly requested.
  - **c3po exceptions** (already requested): `README.md` exists by request; the GUI panel may use light emoji — but code stays emoji-free.

## Change log

- **Every change updates `CHANGELOG.md` in the same commit.** Follow the existing
  `## [x.y.z] - YYYY-MM-DD` + `### Added / Changed / Fixed` format.
- Work that is not part of a release goes under `[Unreleased]`. Do not consume a
  version number ahead of an actual release.
- Record **why**, not just what — especially for a fix. A one-line "fixed X" is
  useless six months later; the diagnosis is the valuable part. Include the
  evidence that identified the root cause (measured numbers, exact error text)
  when there is one, since that is what makes a regression recognisable.

## Git conventions

These are this repository's established practices; follow them for any git operation.

### Conventional Commits

Format: `<type>: <subject>`, with a body explaining "why / what" when useful.

- Common types: `feat`, `fix`, `docs`, `refactor`, `chore`
- Subject in English, imperative mood, concise; the optional body covers motivation
  and scope
- Examples:

  ```
  docs: add README and repository scaffolding
  feat: add macOS launcher app with generated icon
  fix: pin arm64 slice so the GUI launch can load native wheels
  ```

### Atomic commits

Group by logic — one commit does one kind of change (e.g. docs / core config /
scripts separately). Do not mix unrelated changes into one commit.

### Linear history

Use rebase when integrating upstream changes (`git config pull.rebase true`) to
avoid pointless merge commits.

### Commit timing and safety

- **Only commit / push when the user explicitly asks.** Never commit or push on
  your own initiative.
- **Never force-push** over remote history (unless the user explicitly asks and
  understands the consequences).
- Never commit secrets. `.env` holds a real Bedrock API key and is gitignored —
  verify with `git status` before every commit that it is not staged.
- Pre-push self-check: `git status` clean, `git log --oneline` sensibly grouped.

## c3po-specific constraints

- **Do not fork WhisperLiveKit.** Reuse it: embed `TranscriptionEngine` / `AudioProcessor`, and serve its UI via `get_inline_ui_html()` with our panel injected. Upgrades must stay drop-in.
- **Verify Bedrock model id / region / model access before hardcoding.** Reply suggestions use `global.anthropic.claude-haiku-4-5-20251001-v1:0`; auth via `AWS_BEARER_TOKEN_BEDROCK` (a Bedrock API key, not full AWS credentials).
- **Translation stays fully local.** Only short text snippets (recent transcript + context) go to Bedrock — never audio.
- `nllw` is a required separate dependency for translation (not pulled in by `whisperlivekit[mlx-whisper]`).

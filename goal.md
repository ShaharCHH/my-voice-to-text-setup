# Hebrew → English Voice Prompting

Speak Hebrew (mixed with English tech terms), get a clean English prompt pasted into whatever I'm typing in (Claude, Claude Code, etc.). Free, local-first, Wispr Flow-style.

## Goal

Push-to-talk hotkey → speak → release → English prompt appears at the cursor.

- Input: Hebrew and English used interchangeably: English terms mid-sentence (`oc`, ArgoCD, Trident, HAProxy, RHCOS...), and whole English clauses or sentences between Hebrew ones
- Output: clean technical English, written as a prompt. No filler, no preamble, tool names and commands untouched.
- Runs on my machine, no mandatory cloud.

## Environment

- MacBook Pro M1, 32GB RAM, macOS
- Prefer offline. Cloud LLM (BYOK) is acceptable for the cleanup step only if local quality is bad. Personal machine only, not a restricted work machine.
- Needs Microphone + Accessibility permissions to paste into fields.

## Pipeline (what has to happen)

1. Capture audio on hotkey
2. STT: Whisper large-v3-turbo (fallback: large-v3, Hebrew fine-tunes), via Metal (whisper.cpp or MLX). Language setting is an eval variable: forced `he` vs auto-detect, with a vocabulary `initial_prompt` in both
3. LLM pass: translate to technical English, strip fillers, preserve terms, output prompt only
4. Paste into active app

Why not Whisper's built-in `translate` task: no context, tends to mangle technical terms and drop English fragments. Transcribe in Hebrew, then translate with an LLM. Keep `translate` as the zero-effort baseline to compare against.

## Plan

### Phase 1: Off-the-shelf (no code)

Try these before building anything.

- **OpenWhispr**: local Whisper + Agent mode (custom system prompt) = the whole pipeline in one app
- **Handy**: Whisper + `translate to English` setting, no LLM pass. Baseline.

Compare on the test set below. If OpenWhispr meets the bar, stop here.

### Phase 2: Custom build (only if Phase 1 falls short)

Small menubar/CLI tool: hotkey → record → whisper.cpp → LLM → paste. This doubles as a learning project for building with LLMs, so structure it with that in mind.

- Language: Python or Swift, my call after Phase 1 findings
- STT: whisper.cpp or mlx-whisper
- LLM step: pluggable backend (Ollama local / Claude API)
- Keep the system prompt in a file, versioned, easy to iterate

## Eval (do this first, it drives every decision)

Build a small test set before comparing anything:

- 20-30 recorded utterances, 5-30 seconds each, my real speech: pure Hebrew, pure English, Hebrew + English terms, alternating Hebrew/English sentences, switching mid-clause, fast speech, long prompts
- STT candidates (same recordings, each with forced `he` and auto-detect):
  1. stock large-v3-turbo (whisper.cpp ggml)
  2. stock large-v3 (whisper.cpp ggml)
  3. `ivrit-ai/whisper-large-v3-turbo-ggml` (Hebrew fine-tune, Apache-2.0, runs in whisper.cpp). Fallback if ggml misbehaves: `-ct2` build via faster-whisper (CPU-only on Mac, so slower). No MLX build exists; converting it yourself is unverified.
- Extra checks: score English-only parts as "kept as-is, not mangled or translated to Hebrew and back"; include 2-3 silent/noisy clips (Whisper hallucinates on silence)
- Reference English output written by hand for each
- Score per setup: term preservation (did `ArgoCD` survive?), meaning correctness, filler removal, latency
- Record results in `RESULTS.md`

Target: latency under ~5s for a 10s utterance, and no mangled tool names in the test set.

## Starter system prompt (for the LLM step)

```
The input is a speech transcript in Hebrew mixed with English. 
Translate to clear technical English.
Keep tool names, commands, flags, and identifiers exactly as spoken (oc, kubectl, ArgoCD, Trident, HAProxy, RHCOS).
Remove filler words and false starts.
Write it as a direct prompt to an AI assistant.
Return only the prompt, no preamble or explanation.
```

## Open questions

- Is large-v3-turbo good enough for my Hebrew, or do I need full large-v3?
- Local LLM (Qwen/Gemma via Ollama) vs Claude Haiku for the cleanup step: quality gap on technical phrasing?
- Does OpenWhispr's Agent mode handle the prompt reliably, or does it add chatter?
- Forced `he` vs auto-detect: which survives interchangeable Hebrew/English better? Do whole English sentences get transliterated into Hebrew letters under forced `he`, and does auto-detect misclassify mixed windows?
- Does a Hebrew fine-tune (ivrit-ai) handle English segments, or only help pure Hebrew? Its model card says nothing about English/code-switching, so this is untested.

## Working notes for Claude Code

- Propose install commands and wait for my go-ahead before running them.
- Start with the eval set scaffolding (`eval/` folder, script to run a setup over the recordings and score it) before touching any pipeline code.
- Flag anti-patterns as you see them; I'm strong on engineering but new to LLM app work.
- Keep docs minimal. Update `RESULTS.md` as findings come in rather than writing long reports.

## Suggested layout

```
voice-prompt/
├── PROJECT.md
├── RESULTS.md
├── prompts/        # system prompts, versioned
├── eval/
│   ├── audio/
│   ├── reference/
│   └── run_eval.py
└── src/            # Phase 2 only
```

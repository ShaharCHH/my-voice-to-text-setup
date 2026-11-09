#!/usr/bin/env bash
# One-command setup for Voice Prompt on a Mac (Apple Silicon). Safe to re-run: it only
# fixes what is missing. Use --check to look without changing anything.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD
WHISPER_DIR="$HOME/models/whisper"
WHISPER_MODEL="ggml-large-v3-turbo.bin"
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL"
APP="dist/Voice Prompt.app"
PIP_PINS=("pyobjc-core<12" "pyobjc-framework-Cocoa<12" "pyobjc-framework-Quartz<12"
          "pyobjc-framework-ApplicationServices<12" "pyobjc-framework-CoreText<12")
PIP_PKGS=(sounddevice pynput numpy rumps py2app)

CHECK=0
WITH_LLAMA3=0
MISSING=0

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [--check] [--with-llama3]

  --check       only report what is missing; change nothing (exit 1 if anything is)
  --with-llama3 also pull llama3 (only needed to re-run the model comparison in eval/)
  -h, --help    show this help
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --with-llama3) WITH_LLAMA3=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

ok()   { printf '  ok       %s\n' "$1"; }
todo() { printf '  MISSING  %s\n' "$1"; MISSING=$((MISSING + 1)); }
run()  { printf '  +        %s\n' "$*"; "$@"; }

# fix <description> <command...>: in --check mode only report, otherwise run the command
fix() {
  local desc=$1
  shift
  if [ "$CHECK" -eq 1 ]; then todo "$desc"; else todo "$desc (fixing)"; run "$@"; fi
}

echo "== platform"
if [ "$(uname -s)" != "Darwin" ]; then echo "macOS only." >&2; exit 1; fi
if [ "$(uname -m)" = "arm64" ]; then ok "Apple Silicon"; else echo "  warning: not Apple Silicon; untested, models will be slow"; fi

echo "== homebrew packages"
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh (not installed automatically)" >&2
  exit 1
fi
for formula in whisper.cpp portaudio ollama; do
  if brew list --formula "$formula" >/dev/null 2>&1; then ok "$formula"; else fix "$formula" brew install "$formula"; fi
done

echo "== ollama server and models"
if curl -sf -m 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
  ok "ollama is running"
else
  fix "ollama is not running" brew services start ollama
  [ "$CHECK" -eq 1 ] || sleep 3
fi
models=(gemma3:12b qwen2.5:7b)
[ "$WITH_LLAMA3" -eq 1 ] && models+=(llama3:latest)
for m in "${models[@]}"; do
  # awk reads all input, so no SIGPIPE under pipefail (grep -q would exit early)
  if ollama list 2>/dev/null | awk -v m="$m" 'NR > 1 && $1 == m { f = 1 } END { exit !f }'; then
    ok "model $m"
  else
    fix "model $m" ollama pull "$m"
  fi
done

echo "== whisper model"
if [ -s "$WHISPER_DIR/$WHISPER_MODEL" ]; then
  ok "$WHISPER_DIR/$WHISPER_MODEL"
else
  fix "$WHISPER_MODEL" bash -c "mkdir -p '$WHISPER_DIR' && curl -L --fail -o '$WHISPER_DIR/$WHISPER_MODEL' '$WHISPER_URL'"
fi

echo "== python environment"
if [ -x .venv/bin/python ]; then ok ".venv"; else fix ".venv" python3 -m venv .venv; fi
if [ -x .venv/bin/python ] && .venv/bin/python -c "import sounddevice, pynput, numpy, rumps, py2app" >/dev/null 2>&1; then
  ok "python packages"
else
  todo "python packages"
  if [ "$CHECK" -eq 0 ]; then
    run .venv/bin/python -m pip install --quiet --upgrade pip
    # pyobjc pinned below 12: it has no prebuilt wheel for Python 3.9 and fails to compile
    run .venv/bin/pip install --quiet "${PIP_PINS[@]}" "${PIP_PKGS[@]}"
  fi
fi

echo "== app bundle"
built_for=""
if [ -f "$APP/Contents/Info.plist" ]; then
  built_for=$(/usr/libexec/PlistBuddy -c "Print :LSEnvironment:VOICEPROMPT_ROOT" "$APP/Contents/Info.plist" 2>/dev/null || true)
fi
if [ "$built_for" = "$ROOT" ]; then
  ok "$APP (points at this folder)"
else
  # the alias-mode app reads the source from the folder it was built in, so a moved repo needs a rebuild
  fix "$APP missing or built for another folder" .venv/bin/python setup.py py2app -A
fi

echo "== launcher link (so Raycast / Launchpad can find the app)"
LINK="$HOME/Applications/Voice Prompt.app"
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "  warning: $LINK exists and is not a link; leaving it alone"
elif [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$ROOT/$APP" ]; then
  ok "$LINK"
else
  # single quotes on purpose: $1 and $2 are expanded by the inner bash, not here
  # shellcheck disable=SC2016
  fix "$LINK" bash -c 'mkdir -p "$(dirname "$2")" && ln -sfn "$1" "$2"' _ "$ROOT/$APP" "$LINK"
fi

echo
if [ "$MISSING" -gt 0 ] && [ "$CHECK" -eq 1 ]; then
  echo "$MISSING item(s) missing. Run scripts/install.sh to fix them."
  exit 1
fi
cat <<NEXT
Ready. Start it:   open "$APP"
First run only: System Settings > Privacy & Security > add "Voice Prompt" to
  Microphone, Accessibility and Input Monitoring, then quit and reopen it.
Terminal version:  .venv/bin/python src/app.py --mode english
NEXT

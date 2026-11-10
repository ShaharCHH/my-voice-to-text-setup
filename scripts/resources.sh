#!/usr/bin/env bash
# Show what Voice Prompt costs your Mac right now: CPU and memory of the app, Whisper and
# Ollama, plus which models are loaded. Read-only. Use --watch to keep it updating.
set -euo pipefail

INTERVAL=0
usage() {
  cat <<'USAGE'
Usage: scripts/resources.sh [--watch [SECONDS]]

  (no option)       print one snapshot and exit
  --watch [SECONDS] refresh every SECONDS (default 2); Ctrl+C to stop

Tip: start --watch, then dictate. whisper-cli only appears for the ~2 seconds it runs.
USAGE
}

case "${1:-}" in
  "") ;;
  --watch) INTERVAL=${2:-2} ;;
  -h|--help) usage; exit 0 ;;
  *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
esac
case "$INTERVAL" in
  ''|*[!0-9]*) echo "SECONDS must be a whole number" >&2; exit 2 ;;
esac

snapshot() {
  echo "Voice Prompt resources   $(date '+%H:%M:%S')"
  echo
  # one ps call, grouped by a pattern per row. RSS = memory actually held in RAM (KB in ps).
  # awk skips its own command line, which contains these patterns.
  ps -axo pid=,pcpu=,rss=,command= | awk '
    function add(k) { n[k]++; cpu[k] += $2; mb[k] += $3 / 1024 }
    /awk/ { next }
    /Voice Prompt\.app\/Contents\/MacOS/ { add("1 Voice Prompt app") }
    /whisper-cli/                        { add("2 whisper-cli (only while transcribing)") }
    /ollama serve|\/bin\/ollama/         { add("3 ollama server") }
    /llama-server|ollama runner/         { add("4 ollama model runner (the loaded LLM)") }
    END {
      printf "%-42s %5s %7s %9s\n", "PROCESS", "COUNT", "CPU %", "MEM (MB)"
      for (k = 1; k <= 4; k++)
        for (name in n) if (substr(name, 1, 1) == k)
          { printf "%-42s %5d %7.1f %9.0f\n", substr(name, 3), n[name], cpu[name], mb[name]; tc += cpu[name]; tm += mb[name]; shown[k] = 1 }
      for (k = 1; k <= 4; k++) if (!shown[k]) {
        split("Voice Prompt app|whisper-cli (only while transcribing)|ollama server|ollama model runner (the loaded LLM)", lab, "|")
        printf "%-42s %5s %7s %9s\n", lab[k], "-", "-", "-" }
      printf "%-42s %5s %7.1f %9.0f\n", "TOTAL", "", tc, tm
    }'
  echo
  echo "Models loaded in Ollama (what is using the big memory):"
  if curl -sf -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    ollama ps | sed 's/^/  /'
  else
    echo "  Ollama is not running"
  fi
  echo
  free=$(memory_pressure 2>/dev/null | awk -F': ' '/free percentage/ {print $2}')
  echo "System memory free: ${free:-unknown}"
}

if [ "$INTERVAL" -eq 0 ]; then
  snapshot
else
  while true; do
    printf '\033[H\033[2J'
    snapshot
    echo
    echo "refreshing every ${INTERVAL}s, Ctrl+C to stop"
    sleep "$INTERVAL"
  done
fi

# Results

Raw outputs live in `eval/results/` (gitignored, real speech). Summarise findings here.

## STT-only, 4 cases (all mixed Hebrew + English), 2026-11-09

Score = English terms kept in Latin spelling in the raw transcript (c01: Trident/PVC/NFS, c02: pod/kubectl/oc, c03: LLM, c04: PXE; 8 total). Latency includes model load.

| setup | terms kept | sec per case (audio 20-39s) | notes |
|---|---|---|---|
| turbo (he / auto) | 5/8 | 2.0-3.4 (auto +~0.8) | `kubectl` heard as "QCTL", PXE as "פיקסי בוט" |
| large-v3 (he / auto) | 5/8 | 4.6-8.3 (auto +~0.5) | only one to write `kubectl`; wrote LLM as "אלה לם"; PXE as "PixyBoot" |
| ivrit-ai turbo (he / auto) | 4/8 | 2.0-3.3 (auto +~0.8) | best Hebrew words (e.g. "אייפון", "מבוססים"); mangled English: "ever bad on my pod", "project here" |

Findings:
- `he` and `auto` gave identical text on all 4 cases (they are mostly Hebrew, so auto picks Hebrew). Auto only costs latency here. Needs English-heavy clips before it tells us anything.
- No clear STT winner on 4 cases. All three are well inside the latency target (turbo/ivrit about 0.1x audio length, large about 0.25x).
- Most "missed" terms are Hebrew-letter spellings (אוסי = oc, פיקסי בוט = PXE, QCTL = kubectl). That is the LLM step's job, so it goes in the glossary: `prompts/v2.md` adds the ones seen here.
- Unrecoverable by glossary: c01 "for the project year" in every setup (English phrase garbled).

## Next
- LLM step on saved transcripts (needs a backend: Haiku API key or Ollama).
- Record pure-English, alternating and silent clips to separate `he` from `auto`.

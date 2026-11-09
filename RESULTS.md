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

## Prompt test set, 33 text cases (`eval/prompt_cases.json`), 2026-11-09

Pass = all required terms present, no forbidden terms, nothing dropped or answered. v4-en / v5-en are the English prompts.

| prompt + model | pass | avg sec |
|---|---|---|
| v4-en + gemma3:12b | 32/32* | 5.0 |
| v4-en + llama3 8B | 28/32* | 1.0 |
| v5-en + gemma3:12b | 29/32* | 5.5 |
| v5-en + llama3 8B | 22/32* | 1.5 |
| glossary only (no LLM) | 22/33 | 0.0 |

\*first 32 cases; `rw1` (a real dictation) was added afterwards. Llama 3 obeyed injected instructions (answered "you are now a pirate" as a pirate, printed its system prompt). Gemma did not.

**Caution:** these inputs are written the way people talk ("um so I need you to like..."), but Whisper already removes most of that, so the set overstates what an LLM can fix.

## Real dictations beat the synthetic set (6 samples)

Words kept from Whisper's text after the rewrite step (100% = nothing lost):

| dictation | llama3 | gemma | glossary |
|---|---|---|---|
| #2 (68 words, three mentions of "kube deck") | 60% | 38% | 96% |
| #3 (67 words) | 72% | 46% | 99% |
| #4 (27 words, a request with the reason) | 15% | 100% | 96% |

Gemma, perfect on the synthetic set, drops the most on real speech. Decision: English mode uses the glossary only; the LLM stays for Hebrew (translation) and as an opt-in experimental mode. Glossary weaknesses (synthetic set): spoken commands 0/3, self-corrections 0/3, long 1/4.

## Qwen 2.5 7B (added later the same day)

| prompt v4-en, 33 cases | pass | avg sec |
|---|---|---|
| qwen2.5:7b | 31/33 | 1.0 |
| llama3 8B | 27/33 | 1.1 |
| gemma3:12b | 32/33 | 5.1 |

(Judge fixed after the first run: a correct self-correction was flagged "too short", and plural "terabytes" was not accepted. Applied to all models.) On real English dictations Qwen kept 81-87% of the words, never turned "cube deck" into `kubectl`, and kept every meaning; it still drops a clause now and then (the "which I cannot see in the Kube Deck" reason in #2). Injection cases 3/3, spoken commands 3/3.

Hebrew (4 clips, v3 prompt, LLM seconds after warm-up): qwen 2.8 / 5.1 / 2.9 vs gemma 10.1 / 14.1 / 8.2. Quality about equal: qwen got the c02 self-correction right, gemma was cleaner on c04, qwen missed `QCTL` -> `kubectl`. Added as the mode "Hebrew + English (faster, Qwen)" to compare in real use; the default stays Gemma until there is more data.

## Next
- Use "Hebrew + English (faster, Qwen)" for a few days; compare with Gemma via src/transcripts.py before changing the default.
- Idea: LLM with a safety net, falling back to the glossary text when the rewrite keeps under ~85% of the words.
- Record pure-English, alternating and silent clips to separate `he` from `auto`.

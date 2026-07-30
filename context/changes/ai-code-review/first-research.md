# AI code review agent — pierwsze rozeznanie (research + plan v1)

Data: 2026-07-30

## Cel

Napisać w Pythonie pętlę agentową (Claude Agent SDK) do automatycznego
review kodu w PR-ach tego repo. Docelowo uruchamiana w workflow przy każdym
PR: pobiera diff, dodaje komentarze do PR-a przy odpowiednich liniach kodu,
uwzględnia zarówno jakość kodu jak i zgodność z zasadami z `AGENTS.md`.

**Ta iteracja** skupia się wyłącznie na samej pętli agentowej — uruchamianej
lokalnie, bez wpinania w CI/CD i bez realnego postowania komentarzy na
GitHubie (tryb dry-run).

Zasada przewodnia z briefu: jak najwięcej zwykłego, deterministycznego kodu
Python — pobranie diffa, parsowanie diffa, budowa promptu i (docelowo)
publikacja komentarzy to kod, nie wywołania narzędzi przez agenta. Jedyna
część "agentowa" to faktyczna ocena kodu przez model, plus opcjonalne
odczyty repo (Read/Grep/Glob) po dodatkowy kontekst wokół zmienionych linii.

Repo nie ma jeszcze katalogu `tools/`/`scripts/` ani żadnego użycia Claude
Agent SDK — to greenfield.

## Zweryfikowane fakty o Claude Agent SDK (Python)

Skill `claude-api` dostępny w tym środowisku jawnie NIE obejmuje Claude
Agent SDK (to osobny produkt z własną dokumentacją), więc poniższe zostało
zweryfikowane niezależnie (WebFetch oficjalnej dokumentacji SDK), a nie
zgadywane z pamięci modelu:

- Pakiet `claude-agent-sdk` (PyPI), Python 3.10+. **Sam w sobie zawiera
  natywny binarny Claude Code** dla danej platformy — NIE trzeba osobno
  `npm install -g @anthropic-ai/claude-code`.
- Wzorzec headless "jednorazowego przebiegu": `async for message in
  query(prompt=..., options=...)`. `ClaudeSDKClient` służy do
  wieloturowej/interaktywnej rozmowy — tu niepotrzebny.
- Istotne pola `ClaudeAgentOptions`: `system_prompt`, `cwd`,
  `allowed_tools`, `disallowed_tools`, `permission_mode`, `mcp_servers`,
  `model`, `max_turns`.
- Dla pełnej, nieinteraktywnej blokady tylko-do-odczytu:
  `allowed_tools=["Read", "Grep", "Glob", "mcp__reviewer__submit_finding"]`
  + `permission_mode="dontAsk"` (wymienione narzędzia auto-zatwierdzane,
  wszystko inne odrzucane bez pytania — brak zawieszenia, brak promptu).
  **Nie** `bypassPermissions` — ten tryb ignoruje `allowed_tools` i
  pozwoliłby też na Bash/Write/Edit. Dodatkowo (defense-in-depth):
  `disallowed_tools=["Bash","Write","Edit","NotebookEdit","WebSearch","Task"]`
  — reguły odmowy wygrywają nawet gdyby allow-lista została kiedyś
  poluzowana przez pomyłkę.
- Custom narzędzie do zebrania ustrukturyzowanego wyniku: dekorator
  `@tool(name, description, input_schema)` + `create_sdk_mcp_server(name=...,
  version=..., tools=[...])`, zarejestrowane przez
  `mcp_servers={"reviewer": server}`, dostępne w `allowed_tools` jako
  `mcp__reviewer__submit_finding`.
- Handler narzędzia działa **w tym samym procesie** — może po prostu
  `.append()`ować do zwykłej listy Pythona (closure). Nie trzeba parsować
  strumienia wiadomości, żeby wyciągnąć dane strukturalne — naturalnie pasuje
  do zasady "jak najwięcej w kodzie".
- GitHub REST `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews`
  przyjmuje `comments[]` w formie `{path, line, side, body}` (`side`:
  `LEFT`/`RIGHT`) — bez liczenia starego "diff position". Potwierdza, że
  kształt `Finding` poniżej jest gotowy na przyszłe wpięcie w realne
  publikowanie.

## Proponowana struktura katalogów

```
tools/
  pr-review-agent/
    pyproject.toml
    .python-version              # "3.13", zgodnie z backend
    README.md                    # setup, ANTHROPIC_API_KEY, użycie
    src/pr_review_agent/
      __init__.py
      cli.py                     # argparse, orkiestracja
      github_diff.py             # subprocess wrapper na `gh`
      diff_parser.py             # unified diff -> model zmienionych linii
      agents_context.py          # wczytuje AGENTS.md, buduje system prompt
      review_agent.py            # ClaudeAgentOptions, @tool, pętla query()
      models.py                  # dataclasses Finding / ReviewOutput
      output.py                  # console + JSON + markdown (dry-run)
    tests/
      test_diff_parser.py
      fixtures/sample.diff
```

Własny `pyproject.toml` (uv, Python 3.13) izoluje zależność od Agent SDK od
`backend/pyproject.toml` — nie ma powodu dorzucać jej do drzewa zależności
aplikacji FastAPI.

**Znana luka, nie naprawiana teraz:** hook ruff w `.pre-commit-config.yaml`
jest ograniczony do `files: ^backend/`, więc to nowe drzewo nie będzie
automatycznie lintowane — zadziałają tylko ogólne hooki (whitespace/EOF/
yaml/toml) obejmujące całe repo. Do odnotowania w README narzędzia jako
follow-up przy wpinaniu w CI.

## Projekt modułów

**`github_diff.py`** (zwykły kod, bez agenta):
- `get_pr_metadata(pr_number, repo=None)` → `gh pr view <n> --json
  number,title,url,baseRefName,headRefName,files [-R repo]`.
- `get_pr_diff(pr_number, repo=None, exclude=None)` → `gh pr diff <n>
  --patch [-e glob ...]`. `-e/--exclude` to realna flaga `gh` — do
  wykluczania szumu (`uv.lock`, `package-lock.json`, migracje).
- Jasny wyjątek `GhCommandError` z treścią stderr przy błędzie (brak
  autoryzacji, brak PR-a itd.).

**`diff_parser.py`**:
- Biblioteka `unidiff` (`PatchSet`) zamiast własnego parsera — to moduł
  krytyczny dla poprawności (złe numery linii = źle zakotwiczone komentarze),
  więc lepiej oprzeć się na gotowej, przetestowanej bibliotece.
- `ChangedFile`: `path`, `is_added`, `is_removed`, `is_renamed`,
  `hunks_text`, `added_line_numbers` (poprawne kotwice `side="RIGHT"`),
  `removed_line_numbers` (`side="LEFT"`).
- `build_diff_context(files, max_chars)` → `(text, was_truncated)` do
  budowy promptu; przy ucinaniu usuwać całe pliki niskiego priorytetu
  (lockfiles, migracje), nie ucinać w środku pliku, zawsze zachować pełną
  listę plików żeby agent wiedział co zostało pominięte.
- Ten moduł dostaje realne testy `pytest` (`tests/test_diff_parser.py` +
  fixture diff) — to czysta logika i jedyne miejsce gdzie błąd cicho psuje
  wszystkie dalsze findingi.

**`agents_context.py`**:
- `find_repo_root()` przez `git rev-parse --show-toplevel`.
- `load_agents_md(repo_root)` czyta `AGENTS.md`; czytelny błąd gdy brak.
- `build_system_prompt(agents_md)` składa: stwierdzenie roli ("recenzuj
  WYŁĄCZNIE dostarczony diff; nigdy nie napraw kodu; nigdy nie modyfikuj
  plików"), pełną treść `AGENTS.md` dosłownie pod wyraźnie oddzieloną sekcją
  "Zasady repozytorium", jawne wskazówki użycia narzędzi (Read/Grep/Glob
  tylko po dodatkowy kontekst, nigdy do przeglądania niepowiązanego kodu),
  kontrakt `submit_finding` (jedno wywołanie na problem, brak innego
  wymaganego outputu).

**`models.py`**:
```python
Finding(path: str, line: int, side: Literal["LEFT","RIGHT"],
         severity: Literal["blocker","warning","nit"], comment: str,
         rule_reference: str | None = None)
ReviewOutput(pr_number: int, event: Literal["COMMENT","REQUEST_CHANGES","APPROVE"],
             summary_body: str, comments: list[Finding])
```
`ReviewOutput.comments` mapuje się 1:1 na przyszły payload `comments[]`;
`event`/`summary_body` na pola tego endpointu — wpięcie realnej publikacji
później to mały, kontrolowany dodatek.

**`review_agent.py`** — właściwa pętla:
- `findings: list[Finding]` zbierane przez closure w handlerze `@tool`
  `submit_finding` (waliduje `side`/`severity`, dodaje, zwraca krótkie
  potwierdzenie).
- `server = create_sdk_mcp_server(name="reviewer", version="0.1.0",
  tools=[submit_finding])`.
- `options = ClaudeAgentOptions(system_prompt=..., cwd=str(repo_root),
  allowed_tools=["Read","Grep","Glob","mcp__reviewer__submit_finding"],
  disallowed_tools=[...], permission_mode="dontAsk",
  mcp_servers={"reviewer": server}, model=args.model,
  max_turns=args.max_turns)`.
- Prompt: tytuł/URL/branche PR-a + (ewentualnie ucięty) tekst diffa +
  jednolinijkowe przypomnienie instrukcji.
- `async for message in query(...)`: log tekstu asystenta na poziomie
  verbose, przechwycenie końcowego `ResultMessage` (status, liczba
  turów) do ustalenia kodu wyjścia CLI.

**`output.py`**:
- Treść podsumowania **budowana w kodzie** z zebranych findingów (np.
  "Znaleziono 3 uwagi (1 blokująca, 2 drobne) w 2 plikach"), nie przez
  agenta — bez dodatkowego wywołania modelu, zgodnie z zasadą "jak
  najwięcej w kodzie".
- `print_console` (grupowane po pliku, wyraźny baner `DRY RUN — nie
  opublikowano`), `write_json` (`review-output/pr-<n>-<timestamp>.json`,
  dokładny przyszły kształt payloadu POST), `write_markdown` (wersja
  czytelna dla człowieka).
- Deduplikacja identycznych findingów (ten sam path+line+comment); limit
  domyślny (np. 30) żeby uciec przed zalewem wyników przy rozjechanym
  agencie.

**`cli.py`**:
- `argparse`: `--pr` (wymagane), `--repo`, `--model` (domyślnie
  `claude-opus-5`), `--max-turns` (domyślnie 15), `--max-findings`
  (domyślnie 30), `--exclude` (powtarzalne, domyślnie `uv.lock`,
  `package-lock.json`, `backend/migrations/*`), `--out-dir` (domyślnie
  `./review-output`), `--format {console,json,markdown,all}`, `--verbose`.
- `main()` → `asyncio.run(...)`: pobranie metadanych+diffa → parsowanie →
  budowa system promptu → uruchomienie agenta → budowa `ReviewOutput` →
  zapis wyników.
- Kod wyjścia 0 przy poprawnym przebiegu (zero findingów to też poprawny,
  "czysty" wynik); niezerowy przy błędach `gh`, braku `AGENTS.md`, albo
  `ResultMessage` innym niż `success`.

## Przyjęte domyślne ustawienia (do korekty, nieblokujące)

- **Model:** `claude-opus-5` (domyślny wg wytycznych tej sesji),
  nadpisywalny przez `--model`.
- **Odpowiednik "effort":** Agent SDK nie ma bezpośredniego pokrętła
  effort/thinking-budget; praktyczną dźwignią jest `max_turns` (domyślnie
  15).
- **Mapowanie severity → event** (na przyszłość, zakodowane już teraz żeby
  kształt się nie zmieniał): dowolny `blocker` → `REQUEST_CHANGES`, inaczej
  `COMMENT`.
- **Wykluczenia szumu:** domyślnie `uv.lock`, `package-lock.json`,
  `backend/migrations/*` pomijane w diffie wysyłanym do agenta.

## Weryfikacja (jak sprawdzić że działa)

- `cd tools/pr-review-agent && uv sync` — instaluje `claude-agent-sdk` +
  `unidiff` w izolowanym venv.
- `uv run pytest` — testy `diff_parser.py` przechodzą na fixture diffie
  (poprawne numery dodanych/usuniętych linii per plik).
- Eksport `ANTHROPIC_API_KEY` w shellu (nigdy nie commitować — zgodnie z
  twardą zasadą repo o sekretach) i uruchomienie na już zmergowanym PR-ze z
  historii tego repo (`uv run pr-review-agent --pr <n>`) — `gh pr diff`
  działa też na zamkniętych/zmergowanych PR-ach, a ta iteracja i tak nic nie
  publikuje.
- Sprawdzić: output konsolowy jest wyraźnie oznaczony jako dry-run, kształt
  pliku JSON odpowiada `{event, body, comments: [{path, line, side, body},
  ...]}`, a findingi odnoszą się wyłącznie do linii faktycznie dodanych w
  diffie (spot-check kilku par plik/linia względem surowego diffa).
- Sprawdzić w logu verbose, że agent nie próbuje użyć Bash/Write/Edit
  (powinny być cicho odrzucane przez `permission_mode="dontAsk"` +
  `disallowed_tools`) — to smoke test na to, że blokada faktycznie działa.

## Otwarte kwestie do potwierdzenia

1. Model domyślny (`claude-opus-5`) vs. tańszy/szybszy model do iteracji nad
   samym narzędziem podczas jego budowy.
2. `max_turns` jako proxy na "effort" — czy 15 to sensowny start.
3. Czy dodać twardy limit findingów i deduplikację (proponowane: limit 30,
   dedup identycznych path+line+comment).
4. Taksonomia severity: `blocker`/`warning`/`nit` — czy te etykiety pasują.
5. Czy podsumowanie ma pisać agent (dodatkowe wywołanie) czy kod (zalecane).
6. Domyślne wzorce wykluczeń dla dużych diffów — czy `uv.lock`,
   `package-lock.json`, `backend/migrations/*` to dobry start dla tego repo.

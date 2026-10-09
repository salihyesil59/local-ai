# Offline data

## Kiwix ZIM archives

Kiwix publishes topic archives of Wikipedia and the DevDocs documentation as single files, updated monthly.
`nopic` archives keep the full text and the formulas (as TeX) without images. Put them into `LOCAL_AI_ZIM_DIR`
(default `%USERPROFILE%\wiki-zim`, on Linux `~/wiki-zim`) and restart LM Studio so the servers reload.

| Archive | Size |
|---|---|
| `wikipedia_en_physics_nopic_2026-07.zim` | 318 MB |
| `wikipedia_en_mathematics_nopic_2026-09.zim` | 366 MB |
| `wikipedia_en_astronomy_nopic_2026-08.zim` | 404 MB (cosmology, astrophysics) |
| `wikipedia_tr_physics_nopic_2026-07.zim` / `wikipedia_tr_mathematics_nopic_2026-07.zim` | 64 MB / 33 MB |
| `devdocs_en_<library>_<date>.zim`: python, numpy, pandas, matplotlib, pytorch, scikit-learn, scikit-image, statsmodels, sqlite, git, bash, latex, markdown, requests, c, cpp, cmake | a few MB each |

How the servers use them:

- `wikipedia` reads the `wikipedia_*` files (language from the ZIM metadata), `docs` the `devdocs_*` files.
- `search_library` with `sources=zim` searches passages in all archives.

Browse newer releases at [download.kiwix.org/zim/wikipedia](https://download.kiwix.org/zim/wikipedia/) and
[download.kiwix.org/zim/devdocs](https://download.kiwix.org/zim/devdocs/), or use the data commands below.

## Your library

Put books, lecture notes and papers (PDF / .md / .txt, subfolders included) into `LOCAL_AI_LIBRARY` (default
`%USERPROFILE%\physics-library`, on Linux `~/physics-library`). Free sources that work well:

- David Tong's Cambridge lecture notes ([davidtong.org/teaching](https://davidtong.org/teaching/));
- Sean Carroll's *Lecture Notes on General Relativity* (arXiv:gr-qc/9712019);
- *Scientific Python Lectures* and the SymPy manual (PDF);
- the Python documentation as plain text, since Python no longer publishes PDF docs.

## Data commands (online)

The data commands run in your shell, not in LM Studio, so they read `LOCAL_AI_ZIM_DIR` from the shell. PowerShell,
with the environment activated:

```powershell
$env:LOCAL_AI_ZIM_DIR = "$HOME\wiki-zim"         # permanently: setx LOCAL_AI_ZIM_DIR "$HOME\wiki-zim"
local-ai data zim search physics --lang eng      # Kiwix catalog
local-ai data zim download wikipedia_en_physics_nopic_2026-07.zim
local-ai data zim update                         # newer releases replace old ones (--keep-old to keep)
local-ai data arxiv harvest --set physics        # OAI-PMH, incremental; the first run of a big set takes hours
local-ai data arxiv pdfs 2401.00001 hep-th/9901001 --dest $HOME\papers\arxiv
local-ai data status
```

On Linux: `export LOCAL_AI_ZIM_DIR=~/wiki-zim` and `--dest ~/papers/arxiv`.

ZIM downloads go to the first folder in `LOCAL_AI_ZIM_DIR` unless `--dest` is given. They resume after
interruptions and are checked against Kiwix's SHA-256 files.

The arXiv harvester follows arXiv's rules for bulk access: one request every few seconds, and it honours
Retry-After. Afterwards, run `local-ai-index` on the new PDFs and metadata for the `library` server.

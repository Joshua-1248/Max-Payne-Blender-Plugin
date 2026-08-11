# Licensing, Credits & What's Safe to Upload

Written to make it straightforward to publish the LVL/LDB tooling on
GitHub without stepping on anyone's rights. **This is not legal advice**
— it's an accurate account of what was used, where it came from, and the
generally-understood norms for reverse-engineering/interoperability
projects like this one. If the project grows or gets any commercial
attention, a real lawyer review is worth the money.

---

## 1. The short version

- **Your own code** (the Python parsers, the Blender addon, this
  documentation) — you own it, license it however you like. MIT or
  Apache-2.0 are the normal choices for this kind of tool; see §5.
- **Never commit**: the game's assets, `MaxED.exe`, the tutorial `.chm`,
  the internal `.doc`, or any `.lvl`/`.ldb` file that contains real Max
  Payne level geometry or textures. All of that is Remedy/Rockstar's
  copyrighted material. See §3 for the exact list from this session.
- **Credit, don't redistribute**, the third-party tools that helped
  figure the formats out (Ghidra, CFR, `ldb-to-lvl.jar`). See §2.
- Reverse-engineering a file format to write an *independent,
  interoperable* reader/writer is on solid legal ground in most
  jurisdictions (the US DMCA and EU Software Directive both carry
  explicit interoperability exemptions). Copying someone else's actual
  source code is not — and nothing here does that; see §2.3 for exactly
  where the line was kept.

---

## 2. Third-party tools and resources used

### 2.1 Ghidra
- **What**: NSA's reverse-engineering suite. Only its bundled
  `mfc42.exports` ordinal→name reference table was used (fetched from
  `github.com/NationalSecurityAgency/ghidra`), to resolve `MFC42.DLL`
  import ordinals in `MaxED.exe` to real function names.
- **License**: Apache License 2.0.
- **Used how**: as a reference data file only, not redistributed. If the
  repo ever wants to check this table back in for reproducibility, keep
  Ghidra's copyright header and the Apache-2.0 license text with it.

### 2.2 CFR (Class File Reader) decompiler
- **What**: decompiled `ldb-to-lvl.jar`'s `.class` files back to
  readable Java.
- **Author**: Lee Benfield (`leibnitz27/cfr` on GitHub).
- **License**: MIT.
- **Used how**: as a one-time analysis tool, not redistributed or
  bundled with anything produced here.

### 2.3 `ldb-to-lvl.jar` (LDB→LVL converter)
- **What**: a third-party, independently-written Java tool that
  converts Max Payne `.ldb` files to `.lvl`. Author handle: **artkuznet**
  (from the Java package name `com.artkuznet.converter`). No license
  file was bundled with the jar as provided, and no upstream repository
  was located during this session.
- **How it was used**: decompiled with CFR to *read and understand* its
  approach to the `.lvl` binary layout — specifically the per-polygon
  record structure, which had been an open question across a long
  reverse-engineering effort. The Python reader in this project
  (`lvl_reader.py`) was then **written independently**, field by field,
  cross-checked byte-for-byte against real `.lvl` files rather than
  transcribed from the Java. Variable names and structure were not
  copied verbatim; the Python code reflects the *file format itself*
  (which is not copyrightable — formats and the facts of how bytes are
  laid out are not protected expression) rather than artkuznet's
  particular expression of it.
- **What NOT to do**: don't commit the jar itself, don't commit the
  decompiled `.java` output as project files (it's a decompilation of
  someone else's compiled work, licensing unknown), and don't present
  the Python reader's field names/comments as a verbatim copy of
  artkuznet's source if a specific docstring or comment was lifted
  closely — the addendum (§6) flags a couple of spots worth
  paraphrasing further before publishing.
- **Credit line to use** (see §5 for where): *"LVL polygon record layout
  cross-referenced against artkuznet's `ldb-to-lvl` converter
  (decompiled for research purposes; no upstream repository found at
  time of writing)."*

### 2.4 Wine
- **What**: used to actually run `MaxED.exe` in a Linux sandbox, to
  validate file-format understanding against the real editor (load
  tests, `Export X_LevelDB`, Properties dialogs, etc.).
- **License**: LGPL 2.1.
- **Used how**: as a runtime environment only. `MaxED.exe` itself was
  never modified, redistributed, or included in any output — Wine just
  hosted a copy the person already had.

### 2.5 Python libraries
- **`pefile`** (MIT) — parsed `MaxED.exe`'s PE headers and import table.
- **`olefile`** (BSD) — read the OLE/CFB container format of the `.doc`
  QA notes file.
- Both are standard, widely-used open-source libraries; no special
  attribution beyond a standard `requirements.txt`/dependency note is
  needed.

### 2.6 `io_max_payne_ldb` (the existing Blender addon)
- This is the project's own pre-existing LDB importer (from earlier
  sessions), used here as the validated reference for confirming the
  MaxED-recompiled `.ldb` parses correctly. If this predates the current
  session and has its own authorship/license already, keep that as-is —
  nothing here changes its status.

---

## 3. Remedy/Rockstar material used for research — DO NOT COMMIT

Everything in this list is Remedy Entertainment's (or Rockstar's, as
publisher) copyrighted material, used here only for research and
format-compatibility purposes. **None of it belongs in a public git
repository**, including in commit history (deleting a file in a later
commit does not remove it from git history — see §4).

| Item | Why it's sensitive |
|---|---|
| `MaxED.exe` and the rest of the MaxED install (`MaxED.zip`) | Remedy's proprietary level editor binary and bundled game assets |
| `MaxED_Tutorial_v1_01HH-R1.chm` | Remedy's copyrighted official documentation |
| `ai_test_01.doc` | Internal Remedy QA notes — not public material |
| Any `.lvl` / `.ldb` / `.ai` file that contains real Max Payne level geometry, textures, or embedded bitmaps (`ai_test_01.lvl`, `BasicRoom.lvl`, `SUBWAY_A.LVL`, `Manor_Outside.lvl`, `exit_test_01.ldb`, `recompiled.ldb`, `exit_test_01_from_jar.lvl`, etc.) | Contain copyrighted level design, geometry, and/or embedded copyrighted textures |
| Embedded texture files pulled out of any `.lvl` (e.g. `BETON45.JPG`, `BARREL01.JPG`) | Copyrighted game textures |
| The MFC42 ordinal→ RTTI class names, RE'd strings, and disassembly excerpts *if quoted at length* | Low individual risk (short factual excerpts documenting an interface are generally fine), but avoid pasting large contiguous disassembly listings of `MaxED.exe` into public docs — summarize/describe instead of dumping |

**What's fine to commit instead:** your own Python source code, your own
prose documentation of the file formats (this addendum, the session
report), small illustrative byte snippets used to explain a specific
field (a few bytes, with commentary — standard practice in file-format
documentation, e.g. the Xentax/ZenHAX wikis), and test fixtures you
generate yourself that don't embed real game content (e.g. a
hand-crafted minimal `.lvl` with a single dummy cube and no real
textures, if you want a repo-friendly test file).

---

## 4. Practical GitHub hygiene

- Add a `.gitignore` covering `*.lvl`, `*.ldb`, `*.ai`, `*.chm`, `*.doc`,
  `MaxED.exe`, `MaxED.zip`, and any `outputs/`-style scratch directory,
  so these can't get committed by accident.
- If any of the sensitive files listed in §3 were ever committed to a
  local git repo before reading this, **don't just delete and commit
  again** — the blobs stay in history and are still fetchable. Either
  start the public repo fresh from a clean working tree, or use
  `git filter-repo` (or BFG Repo-Cleaner) to strip them from history
  before the first push.
- A short `NOTICE` or `THIRD_PARTY.md` file in the repo root, pointing
  back to this document (or a trimmed version of it), is the standard
  place to keep §2's credits visible.

---

## 5. Suggested license for your own code

Two common, permissive choices for a reverse-engineering/format-support
tool like this:

- **MIT** — shortest, simplest, most common for small utilities.
- **Apache License 2.0** — adds an explicit patent grant; matches
  Ghidra's license if you want consistency with that credit.

Either is a reasonable default. Whichever is picked, a one-line credit
section in the `README` covering §2's tools (Ghidra, CFR, artkuznet's
converter, Wine) keeps the provenance visible without needing to
redistribute any of them.

Suggested `README` credits section:

```markdown
## Credits

Format research for this project used:
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra) (Apache-2.0) —
  MFC42.DLL ordinal reference data
- [CFR decompiler](https://github.com/leibnitz27/cfr) (MIT) by Lee Benfield
- artkuznet's `ldb-to-lvl` converter — decompiled for research purposes
  to cross-reference the LVL polygon record layout (no upstream
  repository located; please reach out if you're the author and would
  like different credit or removal)
- [Wine](https://www.winehq.org/) (LGPL-2.1) — used to run the original
  MaxED.exe for validation, not redistributed

This project does not include or redistribute any Max Payne game
assets, MaxED.exe, or Remedy's own documentation. It implements support
for Remedy Entertainment's `.lvl`/`.ldb` file formats independently.
```

---

## 6. Spots worth a second look before publishing

- In `lvl_reader.py`'s module docstring and a few inline comments, the
  wording stays close to how the artkuznet source names things
  (`unkVector1`, `getUnknownVectors`, etc.) because those *are* the only
  names available for still-not-fully-understood fields — this is
  normal and fine (you're documenting the format, not the tool), but if
  you want extra distance, rephrase the docstring's framing sentences in
  your own words before the initial public commit.
- The `ldb2lvl_decompiled/*.java` files handed over earlier this session
  are decompiler output of someone else's compiled work — treat them as
  research notes for your own reference, not as repo contents.

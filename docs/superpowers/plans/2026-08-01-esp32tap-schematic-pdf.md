# Esp32Tap Schematic PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export the checked-in Esp32Tap Rev E KiCad schematic as a printable, searchable vector PDF for bench bring-up.

**Architecture:** Treat `Esp32Tap.kicad_sch` as the immutable export source and let KiCad perform the only format conversion. Validate the resulting artifact structurally, textually, and visually without modifying the schematic.

**Tech Stack:** KiCad CLI 10, PDFInfo/Poppler, Git

---

## File structure

- Read only: `hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch` — checked-in Rev E schematic export source.
- Create: `hardware/Esp32Tap/bringup/esp32tap-schematic.pdf` — generated schematic PDF for bench use.
- Create: `docs/superpowers/plans/2026-08-01-esp32tap-schematic-pdf.md` — this implementation plan.

### Task 1: Export the schematic

**Files:**
- Read: `hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch`
- Create: `hardware/Esp32Tap/bringup/esp32tap-schematic.pdf`

- [ ] **Step 1: Record the schematic source status**

Run:

```bash
git status --short -- hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch
```

Expected: no output, confirming the export source has no local modifications.

- [ ] **Step 2: Export all schematic pages with the KiCad drawing sheet**

Run:

```bash
kicad-cli sch export pdf \
  --output "$PWD/hardware/Esp32Tap/bringup/esp32tap-schematic.pdf" \
  "$PWD/hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch"
```

Expected: exit status 0, a `Plotted to` message, and a new `esp32tap-schematic.pdf`. Absolute paths are intentional because this KiCad CLI version can return success without creating the file when given these relative paths. Do not use black-and-white or drawing-sheet exclusion flags; preserve the schematic's normal vector presentation and Rev E title block.

### Task 2: Verify and publish the artifact

**Files:**
- Test: `hardware/Esp32Tap/bringup/esp32tap-schematic.pdf`
- Create: `docs/superpowers/plans/2026-08-01-esp32tap-schematic-pdf.md`

- [ ] **Step 1: Validate the file structure**

Run:

```bash
file hardware/Esp32Tap/bringup/esp32tap-schematic.pdf
pdfinfo hardware/Esp32Tap/bringup/esp32tap-schematic.pdf
```

Expected: `file` recognizes a PDF and `pdfinfo` reports exactly one page with no parse errors.

- [ ] **Step 2: Validate searchable Rev E text**

Run:

```bash
pdftotext hardware/Esp32Tap/bringup/esp32tap-schematic.pdf - | \
  rg 'Esp32Tap Rev E|ESP32-S3 Precor serial-bus tap'
```

Expected: the exported title text is found.

- [ ] **Step 3: Render and inspect a preview**

Run:

```bash
pdftoppm -f 1 -singlefile -png -r 140 \
  hardware/Esp32Tap/bringup/esp32tap-schematic.pdf \
  /tmp/esp32tap-schematic-preview
```

Open `/tmp/esp32tap-schematic-preview.png` with the image viewer. Expected: the complete sheet is visible, symbols and labels are legible when zoomed, and the Rev E title block is present.

- [ ] **Step 4: Confirm the source remains unchanged**

Run:

```bash
git status --short -- hardware/Esp32Tap/kicad/Esp32Tap.kicad_sch
```

Expected: no output.

- [ ] **Step 5: Commit the plan and PDF only**

Run:

```bash
git add \
  docs/superpowers/plans/2026-08-01-esp32tap-schematic-pdf.md \
  hardware/Esp32Tap/bringup/esp32tap-schematic.pdf
git diff --cached --name-only
git commit -m "docs(Esp32Tap): add schematic PDF"
```

Expected: the staged-file list contains only the plan and generated PDF, followed by one commit containing those files.

- [ ] **Step 6: Close the tracked task and push**

Run:

```bash
bd close precor-9_3x-zlf --reason="Exported and verified the Esp32Tap Rev E schematic PDF"
git pull --rebase --autostash
git push
git status --short --branch
```

Expected: the branch is synchronized with its upstream; pre-existing unrelated worktree modifications remain unstaged.

# Esp32Tap Module Test Checklist PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a printable, evidence-recording PDF for the approved Esp32Tap breadboard bench sequence from raw power through bypass-only treadmill testing.

**Architecture:** Maintain one print-oriented HTML source beside the other bring-up documents and render it to PDF with headless Chrome. Organize the procedure as an unpowered preflight followed by Steps 1–10, with explicit source states, expected values, stop conditions, and spaces for measured evidence.

**Tech Stack:** Static HTML/CSS, Google Chrome headless PDF rendering, Poppler verification tools.

---

### Task 1: Create the printable checklist

**Files:**
- Create: `hardware/Esp32Tap/bringup/esp32tap-module-test-checklist.html`
- Create: `hardware/Esp32Tap/bringup/esp32tap-module-test-checklist.pdf`

- [x] **Step 1: Write the print-oriented HTML**

Include the immutable power-source rules, independent RJ45/pass-through audit,
final visual comparison, Steps 1–10, exact transition directions and limits,
firmware identity gates, fail-release tests, explicit temporary-current-harness
sequence, per-terminal thermal records, reset stop conditions, and measurement
blanks for every meter check.

- [x] **Step 2: Render the PDF**

Run headless Chrome with `--print-to-pdf-no-header` and the repository-local HTML URL.

- [x] **Step 3: Verify the artifact**

Run `pdfinfo` and `pdftotext`; confirm every numbered heading, both removable-
jumper rules, all numerical limits, firmware identity fields, fail-release
tests, current-harness sequence, and STOP gates are present. Render every page
to a contact sheet and inspect it for clipping, browser headers, page-break
errors, and adequate writable space.

- [ ] **Step 4: Commit and push**

Commit only the plan, HTML source, and generated PDF, preserving unrelated worktree changes.

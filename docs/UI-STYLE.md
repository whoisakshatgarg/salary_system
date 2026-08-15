# UI style — the instrument panel

**Status: the design system every page follows** (2026-08-15)

APEX THERMOCON machines parts to tolerance. The app should feel the same way:
a calm workshop wall on which every table is an **instrument** — a framed,
self-contained object you read at a glance — never a field of text that happens
to be aligned. One disciplined blue. Status as small engineered dot-chips, not
scattered pastel pills. Numbers in tabular figures, right-aligned, like a
machinist's log.

The test for every screen: **squint at it.** If the data doesn't separate from
the page as a distinct dark-on-light object with a visible frame, the table has
failed. If two blocks compete for attention, one of them is over-dressed.

---

## 1. Tokens

Every page's inline Tailwind config becomes exactly this (replaces the old
two-shade brand):

```html
<script>
  tailwind.config = { theme: { extend: { colors: { brand: {
    50: "#eff6ff", 100: "#dbeafe", 500: "#3b82f6",
    600: "#1d4ed8", 700: "#1e40af", 800: "#1e3a8a" } } } } };
</script>
```

Base `<style>` block on every page (after the existing `[x-cloak]` rule):

```css
[x-cloak] { display: none !important; }
body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
```

- Ground: `bg-slate-100`, text `text-slate-800`.
- The only accent is brand blue. Emerald / amber / rose are **semantic only**
  (good / attention / late-or-bad) and never decoration.
- Identifiers (heat numbers, order numbers, codes, GSTINs) are always
  `font-mono text-xs` — a serial number should look like one.

## 2. The instrument table — the heart of this pass

Every `<table>` on every page gets this treatment, no exceptions:

```html
<!-- the frame: the table is an OBJECT, visibly bounded on all four sides -->
<div class="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
  <div class="overflow-x-auto">           <!-- wide tables scroll inside the frame -->
    <table class="w-full text-sm">
      <thead class="bg-slate-50 border-b-2 border-slate-200">
        <tr class="text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          <th class="px-4 py-2.5">Heat no.</th>
          <th class="px-3 py-2.5 text-right">Value</th>   <!-- numbers: right -->
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100">
        <tr class="even:bg-slate-50/60 hover:bg-brand-50/60 transition-colors">
          <td class="px-4 py-2.5 font-mono text-xs font-semibold text-brand-700">BS-24-8802</td>
          <td class="px-3 py-2.5 text-right tabular-nums font-semibold text-slate-900">₹18,111</td>
        </tr>
      </tbody>
      <tfoot>  <!-- totals, when the page has them -->
        <tr class="bg-slate-50 border-t-2 border-slate-200 font-semibold">
          <td class="px-4 py-2.5 …">…</td>
        </tr>
      </tfoot>
    </table>
  </div>
</div>
```

What makes it read as an entity even on a cluttered page:
- **A visible frame on all four sides** (`rounded-xl border overflow-hidden`) —
  not a card that fades into other cards.
- **A hard 2px rule under the header** and uppercase 11px tracking-wider header
  text: the header band is unmistakably a header, not a first row.
- **Zebra rows** + hairline dividers: rows stay rows at any density. ⚠ With
  Alpine, the `<template x-for>` element stays in the DOM as the tbody's first
  child, so `even:` tints data rows 1/3/5 and `odd:` tints 2/4/6. Pick whichever
  leaves the FIRST data row white (usually `odd:` under a template) and verify
  against the rendered page, not the class name.
- **Hover** `hover:bg-brand-50/60 transition-colors`, and `cursor-pointer` only
  on rows that actually open something.
- **Long tables scroll inside their frame**: wrap tbody's scroll area as
  `max-h-[65vh] overflow-y-auto` with `thead` `sticky top-0 z-10` (add
  `bg-slate-50` to the thead so rows don't show through) whenever a list can
  exceed ~15 rows (employees, usage log, attendance). The page never becomes
  the table.
- Numbers: `tabular-nums`, right-aligned; the figure that matters
  `font-semibold text-slate-900`, its companion (`/ total`, units)
  `text-slate-400`.
- Dim what's absent: zeros and `—` render `text-slate-300`, so filled cells
  carry the eye.
- Every list gets a **footer count line** where useful ("6 heats · 108 rods in
  stock") — bottom band or under the frame in `text-xs text-slate-400`.
- Empty state: one centered block inside the frame — a dim glyph, one line,
  and the primary action if creating is the fix.

## 3. Status = dot-chips

One shape everywhere (replaces mixed pastel pills):

```html
<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium
             bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
  <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span> In stock
</span>
```

Palettes: emerald = good/done · amber = attention/in-progress · rose =
late/rejected · sky = informational (Quotation) · indigo = Invoice · violet =
QC · slate = neutral/draft. Dot colour is the 500, text 700, bg 50,
ring 600/20.

When EVERY row would carry the same chip (employees "Working"), drop the pill
background and keep only `dot + text-slate-500` — seventy green pills is a
wall, seventy dots is a margin note. The exceptional state (Left, Rejected)
keeps the full pill so it pops.

## 4. Controls

- Primary: `bg-brand-600 hover:bg-brand-700 text-white font-semibold rounded-lg
  shadow-sm px-4 py-2 text-sm transition-colors focus-visible:outline-none
  focus-visible:ring-2 focus-visible:ring-brand-600/40`.
- Secondary: `border border-slate-300 bg-white hover:bg-slate-50 text-slate-700
  rounded-lg px-3 py-2 text-sm font-medium shadow-sm transition-colors`.
- Destructive: rose, same anatomy. Row-level actions are small text buttons
  `text-brand-700 hover:underline` — never a big button per row.
- Inputs/selects: `border border-slate-300 rounded-lg px-3 py-2 text-sm
  bg-white focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20
  outline-none transition` (kill default outline once, per page CSS is fine).
- **Navigation is a real link.** Anything that switches WHAT you're looking
  at — a home tile, a view tab, a record tab, a sidebar section — is an
  `<a href>` carrying a URL that reproduces the view (`?tab=…`, `?view=…`,
  `?open=<id>&seg=…`), with `@click.prevent` doing the in-place switch. Left
  click behaves as before; right-click/middle-click "open in new tab" works
  like the rest of the web. Each page's boot() honours the params and strips
  them with `history.replaceState`.
- **Segmented control** for view switches (tabs like All/Quotations/Invoices,
  Stock/Usage, order stages filter): one container
  `inline-flex items-center gap-0.5 bg-white border border-slate-200 rounded-xl p-1 shadow-sm`,
  each option `px-3 py-1.5 rounded-lg text-sm font-medium text-slate-500
  hover:text-slate-800 transition-colors`, active option
  `bg-slate-900 text-white shadow-sm`. Counts as `text-[11px]` in a lighter
  tone after the label.
- Filter bars: ONE compact toolbar row inside a card — controls inline, labels
  as placeholders or `sr-only`, result count on the far right. Never a
  two-storey deck of labelled boxes.

## 5. Hierarchy above the table

- **Page pattern:** dark header bar (keep) → stat strip → toolbar → instrument
  table. Nothing else at the top level.
- **Stat tiles**: white card `rounded-xl border border-slate-200 shadow-sm
  px-4 py-3`, an **eyebrow** (`text-[11px] uppercase tracking-wider
  text-slate-400 font-semibold`), the figure (`text-2xl font-bold tabular-nums
  text-slate-900`), and one small context line only when it says something the
  figure doesn't (`text-xs text-slate-400`). Semantic tint only on the figure
  when it IS a judgement (outstanding = amber-600).
- **Section headers inside cards**: eyebrow + title, not prose:
  `<div class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">Deliveries</div>`
  optionally followed by a `text-sm font-semibold` title line. This replaces
  most grey explainer sentences.

## 6. Copy — the subtext purge

Delete helper text that restates the control or the obvious ("Pick a section
from the left…", "Search by name…" under a search box). Keep a rule the user
cannot infer (over-shipping rolls forward; rate changes affect new costings
only) as ONE short line, or move it to a `title` tooltip on the element it
explains. Never two sentences where five words do. Labels say what happens:
"Save rate", not "Save".

## 7. Motion & feel

`transition-colors duration-150` on rows/buttons/links; `transition-shadow`
on cards. Home tiles: `hover:shadow-md hover:-translate-y-0.5 transition-all`.
Nothing keyframed, nothing that moves more than 2px. Modals keep their
existing x-transition.

## 8. Alpine guardrails (unchanged, they bit us before)

- `template x-if` (never `x-show`) around anything binding a nullable model.
- Backdrop dismissal is `@click.self` on the backdrop, never `@click.outside`.
- Selects prefilled by fetched data need `x-effect="v && $nextTick(() => $el.value = v)"`.
- Sibling `<tr>`s can't share one `x-for` — use `template x-for` around
  `<tbody>` (one tbody per logical row-group).
- Never rename existing JS helpers; add new ones with page-prefixed names.
- z-index ladder: detail 40 · form modal 50 · full-screen 60 · toast 70.
- `@apply` inside a plain `<style>` block is DEAD CSS — the vendored Tailwind
  Play build never processes it. Write real CSS there, or put the utilities on
  the elements. (Three pages shipped with silently-inert `@apply` rules before
  this was caught.)

## 9. Per-page direction

- **Shell/login**: wordmark as machined nameplate — uppercase, wide tracking,
  thin rule under it; card on a very subtle radial slate wash. Home tiles get
  the hover lift + tinted icon squares (keep emoji); deadline panel becomes
  three labelled columns (Overdue / 7 days / 31 days) with a coloured left rail
  each (rose/amber/slate), rows = mono order no + customer + right-aligned
  "in Nd" chip. Users & Access table → instrument treatment.
- **Inventory**: collapse the filter deck to one toolbar; stock column keeps
  its bar but thin track (h-1.5, rounded-full, emerald when full/ok); status →
  dot-chips; heat numbers mono; detail + usage log + lists + material-check
  panel all get framed tables. The check-result panel is a report: give it an
  eyebrow header and a framed per-heat table.
- **Orders**: keep the new segment bars exactly as they are; frame the three
  lists, dot-chip the stages, mono order numbers, unify the stage-filter pills
  into the segmented control.
- **Parts**: list gets frame; AGREED/QUOTED → dot-chips (emerald/sky); files
  count as `📎 n` dim when zero; costing workspace table is the money
  instrument — right-aligned tabular columns, framed, totals row in tfoot,
  ★ customer-rate badge kept.
- **Customers**: frame; dim zero counts; GSTIN mono; detail tabs → segmented
  control; the month-bar chart gets rounded bar tops + hover shade + axis
  labels in 10px.
- **Quotations**: type → dot-chip (indigo Invoice / sky Quotation), status
  draft=slate, sent=amber, paid=emerald; number mono; Print as small secondary
  button with printer glyph; the three count-pills → segmented control.
- **Employees**: sticky header + in-frame scroll (`max-h-[70vh]`), zebra, dim
  the `#` column, leave-scheme text `text-slate-400`, Working as bare
  dot+text (Left keeps full rose pill), footer count line. The wall becomes an
  instrument.
- **Payroll**: sidebar refined — active item as a full-width brand pill, rest
  `text-slate-300 hover:bg-white/5 rounded-lg`, section spacing; kiosk flows
  untouched. Dashboard tiles get eyebrow treatment; delete the "Pick a
  section…" sentence. Every inner table (attendance, advances, salaries,
  rules, pay setup) gets the instrument treatment; attendance/salary grids get
  sticky headers + in-frame scroll.
- **Settings**: each group is a card with eyebrow header; units stay chips;
  the rates list becomes a framed two-column table (name · compact `w-28
  text-right tabular-nums` input) with a small "Save" text-button per row and
  zebra; departments list framed with hover row + right-aligned ×.
- **Help**: generated separately (`tools/build_help.py`) — out of scope here.

## 10. What this pass must NOT do

No behaviour changes, no new endpoints, no renamed functions, no removed
information (de-emphasise, don't delete data), no new libraries, no webfonts.
Print/A4 documents (quotation/invoice/material-doc HTML) keep their own paper
styling — only screen chrome changes.

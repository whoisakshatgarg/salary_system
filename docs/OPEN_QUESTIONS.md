# Open questions

Unresolved items + the default currently in use. When one is answered, move it
to [DECISIONS.md](DECISIONS.md) with the date.

| # | Question | Default in use until answered |
|---|----------|-------------------------------|
| 1 | **Costing refinements** — rates are per operation type, not per machine. Per-machine rates, if wanted, are an addition. (Auto material cost from Inventory heat rates: **done** 2026-08-14, see the bill of materials.) | As built: 21 seeded operations with editable ₹/hr. |
| 2 | **Employee Management UI split** — exact fields editable in Employee Mgmt vs Salary. Owner said profile/status in EM, "financial stuff (PF/ESI, advances)" in Salary, rest to my judgment. | EM edits: name, dept, shift, joining date, active, documents, leave balance view. Salary edits: base salary, PF/ESI applicability + amounts, advances. Attendance entry moves to EM when its UI ships. |
| 3 | **Excel byte-matching** — the CEO/Distribution exports follow the legacy layout but were never compared against a real sample sheet. | Current layouts ship as-is; need one real sample `.xlsx` from the office to fine-tune. |
| 4 | **E-way bill threshold** — should the app warn when a consignment's invoice value crosses the e-way-bill threshold? | Free-text e-way number only, no threshold logic. |
| 5 | **Multi-user beyond two machines** — more accounts now exist; if a third PC is needed, LAN mode (one server, browsers connect) is the natural step. | Single-PC installs + shared-folder sync only. |
| 6 | **Should employees-granted (non-admin) staff edit profiles / add employees?** Today mutations (add, edit, deactivate) are admin-only; EM-granted staff can view everything and manage documents + the leave bank. | Keep admin-only mutations; the UI hides those buttons for non-admins. Relax per-operation later if HR staff need it. |
| 7 | **Repo visibility for self-update** — repo must be public or a token pasted into `config/update.json` on each install. Which will it be? | Assumed public at release time; token path documented in DEPLOY.md. |
| 8 | **Which phone number is right?** The quotation/ack references print Tel **4167651**; the TC/COC/invoice/packing-list references print **4167561** (CONVENTIONS §1). | Each template keeps the number its own reference prints (fidelity); one word from the owner normalizes all templates. |
| 9 | **Reotemp's client code** — their test certificates carry no code; the scheme says `R` + serial. | Auto-assigned on their customer record the first time paperwork needs it; editable on the customer. |
| 10 | **Official Bill of Materials format** — the BOM is the one document with no company reference; a placeholder in the Apex house style is in use, marked PLACEHOLDER on its face. | `Apex_BOM_Template_PLACEHOLDER.xlsx` + numbering `AT/BOM/{FY}/{serial}` + format code `AT/BOM/EXP/01`, all to confirm when the real format lands. |
| 11 | **One COC per shipment or per part?** The reference COC covers a single part for a whole invoice. | One COC per shipment with an editable part/material line; duplicate the paper per part when a customer wants one each. |

## Known issues (accepted for now)
- ~~payroll console errors~~ — **fixed 2026-08-14** (0 on load now). The cause is
  worth remembering: `x-show` only hides an element, so `x-text`/`x-model` on it
  still evaluate and throw through a null model.
- Existing installs that still have the old `temp` account keep it until deleted
  in Users & Access (new seeds don't create it).

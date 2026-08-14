# Workshop ERP — QA sweep, 14 Aug 2026

9 modules driven in a real browser by one agent each; every finding then re-tested
by a second agent instructed to refute it. Only reproducible defects are listed.

**60 confirmed · 4 refuted and dropped · 2 known/pre-existing**

> **Fixed since this sweep** (2026-08-14/15):
> the CRITICAL inventory one (a purchase price the form accepted bricked the
> whole heat register), the payroll page's 11 console errors, and the
> "Edit shows Customer as —" major on quotations — all three were the same class
> of bug in different places. The rest are open; the shortlist worth doing next
> is in [ROADMAP.md](ROADMAP.md) under *Known, not yet fixed*.

## Raw Material Inventory

### [CRITICAL] A purchase price the form accepts (1e308) permanently bricks the module: every heat read returns HTTP 500 and the register renders as empty
- **Steps:** 1. Open http://127.0.0.1:8010/inventory/ as admin. 2. + New heat -> Heat number ZTEST-INV-POISON, Rods received 10, Purchase price 1000 -> Add heat. 3. Click the row to open it, add a usage-log entry (Order ID ZTEST-PO-POISON, Rods 2) so the heat also has history. 4. Click Edit, type 1e308 into 'Purchase price (Rs total)' (the number input accepts it, checkValidity() is true) and click Save changes. 5. The form shows the red text 'Internal Server Error'. 6. Reload the page (F5).
- **Observed:** The PUT returns 500 but the value is committed anyway (update_heat commits before the response is serialised). From then on GET /api/inventory/heats?sort=newest returns 500 and GET /api/inventory/heats/9 returns 500. After the reload the Stock tab shows an EMPTY stat strip and 'No heats match - add the first one with "+ New heat"' - all 7 heats in the store room, including other people's, are invisible. The record cannot be repaired from the UI: there is no row to click, and clicking the heat number in the Usage Log tab (which still lists it) leaves detail null and only flashes 'Internal Server Error'. I could only recover by calling DELETE /api/inventory/heats/{id} directly from the API.
- **Expected:** A price of 1e308 should be rejected by the same validation that already rejects infinity and negatives (or the pro-rata stock_value should be computed without overflowing), and no single stored value should ever be able to make the whole list/detail endpoint 500.
- **Evidence:** 500 PUT /api/inventory/heats/9 ; 500 GET /api/inventory/heats?sort=newest ; 500 GET /api/inventory/heats/9 ; toast 'Internal Server Error' ; table body text 'No heats match - add the first one with "+ New heat".' ; cleanup DELETE /api/inventory/heats/9 -> 200 {"ok":true}. Cause is the pro-rata value 1e308 * remaining / rods_received overflowing to inf (stock_value per row and the stat strip's stock_value), which is not JSON-serialisable. Screenshot: inventory-15-module-bricked.jpg

### [MINOR] Very large 'Rods received' returns a raw HTTP 500 / 'Internal Server Error' instead of a validation message
- **Steps:** 1. Open http://127.0.0.1:8010/inventory/ as admin. 2. + New heat -> Heat number ZTEST-INV-HUGE, leave the default date, type 100000000000000000000 (1e20) into 'Rods received'. The browser considers the field valid (checkValidity() true, no native bubble). 3. Click 'Add heat'.
- **Observed:** POST /api/inventory/heats returns 500 and the form shows the red text 'Internal Server Error'. The heat is not created (no corruption), but the user gets a server-crash message for a data-entry mistake. Every other bad value on this field (0, -3, blank) is handled with a proper message.
- **Expected:** A 400 with a readable message such as 'Rods received must be a whole number' / an upper bound, not a 500.
- **Evidence:** console: 'Failed to load resource: the server responded with a status of 500 (Internal Server Error)'; network: 500 POST http://127.0.0.1:8010/api/inventory/heats; formError == 'Internal Server Error'. (rods_received is passed straight to SQLite, which cannot store an integer larger than 2^63-1.)

### [MINOR] Fractional rods in the usage log produce an unreadable '[object Object]' error toast
- **Steps:** 1. Open a heat with stock left (e.g. ZTEST-INV-A1) from the Stock list. 2. In the 'Usage log' add-entry row leave Type = 'Issued to order', put an Order ID (e.g. ZTEST-PO-FRACTION) and type 2.7 into the 'Rods' box (the box accepts it - the Add entry button is not a form submit, so the browser never validates the step). 3. Click 'Add entry'.
- **Observed:** POST /api/inventory/heats/5/movements returns 422 and the red toast reads literally '[object Object]'. The user is told nothing about what is wrong.
- **Expected:** A readable message, e.g. 'Rods must be a whole number' (the backend already produces exactly that message when the value is non-numeric rather than fractional).
- **Evidence:** network: 422 POST http://127.0.0.1:8010/api/inventory/heats/5/movements; state s.toast == {show:true, msg:'[object Object]', kind:'err'}. FastAPI's 422 body carries a list of error objects, and the API helper does `detail = (await res.json()).detail || detail` then stringifies it. Screenshot: inventory-12-object-object-toast.jpg

### [MINOR] An error toast raised soon after a previous one is hidden almost immediately, so the user never sees the message
- **Steps:** 1. On /inventory/ trigger any toast (e.g. open a heat and click 'Add entry' with the Rods box empty -> 'Rods must be at least 1'). 2. About 3 seconds later trigger a second, different error (e.g. try to issue more rods than remain). 3. Watch the top-right toast.
- **Observed:** The second toast disappears within a fraction of a second - the previous toast's 3.2 s timer fires and clears the shared toast object. Measured directly: after flash('ZTEST first error'), waiting 3.1 s, then flash('ZTEST second error'), 250 ms later s.toast.show was already false and the toast div computed style was display:none, and it never reappears. In practice, in a quick sequence of failed 'Add entry' clicks the refusal messages flash for a moment or not at all, so a user can believe the action just did nothing.
- **Expected:** Each toast should stay visible for its own full duration (the pending hide-timer should be cleared when a new toast is raised).
- **Evidence:** state after 250 ms: {'show': False, 'msg': 'ZTEST second error', 'kind': 'err'}; getComputedStyle(div[x-text=toast.msg]).display == 'none'. Also observed naturally during rapid validation tests, where 5 consecutive 400-refusal toasts were already hidden 900 ms after being raised.

### [MINOR] Heat numbers are only case-sensitively unique - 'ZTEST-INV-A1' and 'ztest-inv-a1' are accepted as two separate heats
- **Steps:** 1. Create a heat with Heat number ZTEST-INV-A1 (any rods count). 2. + New heat again -> Heat number ztest-inv-a1, Rods received 5 -> Add heat.
- **Observed:** The second heat is created ('Heat ztest-inv-a1 saved') and both rows sit in the list side by side; searching 'ZTEST' returns both. The duplicate check (and the DB UNIQUE index) is byte-exact, but the heat number is the user-facing key taken off a mill certificate, so a case slip silently creates a second register entry for the same batch. The same applies to the Lists tab: 'ZTEST Alloy' and 'ztest alloy' can both be added to Material classes and both show up in the Material filter.
- **Expected:** A duplicate heat number differing only in case should be refused with 'Heat ... already exists' (comparison should be case-insensitive), and the dropdown lists should de-duplicate the same way.
- **Evidence:** toast 'Heat ztest-inv-a1 saved'; list rows ['ztest-inv-a1', 'ZTEST-INV-A1', 'BR-88-402', ...]; options.material_class after the list test: [..., 'ZTEST Alloy', 'ztest alloy']

### [MINOR] Usage-log entries accept any calendar date - rods can be issued years before the heat was received or decades in the future
- **Steps:** 1. Open a heat with stock left (ZTEST-INV-C1R, Received 14 Aug 2026). 2. In the add-entry row set Date = 2015-01-01, Order ID ZTEST-PO-PAST, Rods 1 -> Add entry. 3. Repeat with Date = 2099-12-31, Order ID ZTEST-PO-FUTURE, Rods 1.
- **Observed:** Both are accepted ('Log entry added') and stored: the heat's usage log now contains an issue dated 1 Jan 2015 - eleven years before the material arrived - and one dated 31 Dec 2099. Because both the per-heat log and the global Usage Log sort by date descending, the 2099 entry sits permanently at the top of the log and hides real recent activity.
- **Expected:** The date should at least be bounded (not before the heat's date_received, not in the future), or the user should get a warning; only the calendar validity is checked today.
- **Evidence:** movements after the two entries: [["2099-12-31",1,"ZTEST-PO-FUTURE"],["2026-08-14",4,"ZTEST-PO-200"],["2015-01-01",1,"ZTEST-PO-PAST"]]; heat date_received = 2026-08-14

### [COSMETIC] Searching for '%' (or '_') in the stock search box returns every heat instead of no match
- **Steps:** 1. On the Stock tab type % into the Search box and wait for the debounce.
- **Observed:** All 6 heats are returned - the search term is interpolated straight into a SQL LIKE pattern, so LIKE wildcards typed by the user act as wildcards.
- **Expected:** '%' should be treated as a literal character (no heat number contains it, so the expected result is an empty list).
- **Evidence:** q='%' -> ['ztest-inv-a1','ZTEST-INV-A1','BR-88-402','HT-24-3302','HT-24-1157','HT-24-2231'] (identical to the unfiltered list)

## Shell / login / accounts

### [MAJOR] Every login failure shows "Not signed in" instead of the server's real reason ("Invalid username or password")
- **Steps:** 1. Open http://127.0.0.1:8010/ in a fresh browser context and wait for the login card.
2. Type username `admin` and password `nope`.
3. Click "Sign in".
4. Read the red error text under the Password field (Alpine state: document.body._x_dataStack[0].loginError).
Also reproducible with both fields left completely blank (step 2 skipped), and with a username that does not exist.
Direct API check for contrast: fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json','X-Requested-With':'apex-payroll'}, body:JSON.stringify({username:'admin',password:'nope'})})
- **Observed:** The login card displays the red text "Not signed in". The identical message appears for a blank form and for a non-existent username, so the user gets no indication of what actually went wrong. Meanwhile the server correctly returned 401 with body {"detail":"Invalid username or password"} - that message is discarded before it can be shown. Cause: the shared api() helper in frontend/shell/shell.js intercepts status 401 and throws a hard-coded new Error("Not signed in") before the detail is parsed, and doLogin() renders that generic message.
- **Expected:** The login screen should show the server's message, "Invalid username or password". The 401-means-session-expired shortcut in api() should not apply to the /api/login call itself, which uses 401 to mean "these credentials are wrong".
- **Evidence:** server says: [401, '{"detail":"Invalid username or password"}']  /  UI shows: 'Not signed in'  visible: True. Screenshot: shell-14-login-error-text.jpg

### [MINOR] Double-clicking Save in the new-account form returns HTTP 500 from POST /api/users
- **Steps:** 1. Sign in at http://127.0.0.1:8010/ as admin/admin123.
2. Click the "Users & Access" tile.
3. Click "+ Add account".
4. Fill Username = `ztest-shell-dbl0` and Password = `secret123`.
5. Double-click the blue "Save" button (one ordinary fast double-click; do not click once and wait).
6. Watch the network: two POST /api/users requests are issued.
Equivalent scripted repro (fired 5 times, failed 5/5): await Promise.all([fetch('/api/users',o), fetch('/api/users',o)]) with the same username payload.
7. Clean up by deleting the created account.
- **Observed:** One POST returns 200 and the other returns 500 with body "Internal Server Error", in 5 out of 5 runs both via the UI double-click and via two parallel fetches. The account row is created exactly once (no duplicate row, no data corruption), but the server raises an unhandled exception - the UNIQUE constraint on app_user.username fires inside create_user() after its pre-check SELECT has already passed, and nothing converts that into an HTTPException. The shell sets uform.uformError = 'Internal Server Error' while simultaneously flashing the green "Account saved" toast and closing the modal, so the admin sees a success toast for a request that 500'd.
- **Expected:** The losing request should return the same 400 the sequential path returns, i.e. {"detail":"'ztest-shell-dbl0' already exists"}, and the server should never emit a 5xx for a duplicate username. Ideally the Save button should also be disabled while a save is in flight.
- **Evidence:** run 0: http=[200, 500] modal_open=False uformError='Internal Server Error' toast='Account saved' rows_created=1 (identical for runs 1-4). Scripted: run 0..4 all -> 200/500, detail(500)=['Internal Server Error']

### [MINOR] Opening and closing the account dialog leaks Alpine effects: uncaught "Cannot read properties of null (reading 'grants')" errors grow quadratically
- **Steps:** 1. Sign in at http://127.0.0.1:8010/ as admin/admin123 and attach a page.on("pageerror") listener BEFORE navigating.
2. Click the "Users & Access" tile.
3. Click "+ Add account", then click "Cancel" in the modal. Wait ~700ms.
4. Repeat step 3 eight times, counting uncaught page errors after each close.
(Closing via the "X" button, via Cancel, or via a successful Save all trigger it - anything that sets uform = null.)
- **Observed:** The first close throws nothing, but every subsequent close throws 8 more uncaught TypeErrors than the previous one - one per module checkbox in the grants grid: close #1 = 0, #2 = 8, #3 = 16, #4 = 24, #5 = 32, #6 = 40, #7 = 48, #8 = 56, running total 224 uncaught "Cannot read properties of null (reading 'grants')" exceptions after only 8 open/close cycles. The reactive effects behind :checked="uform.grants.includes(m.key)" (frontend/index.html, inside <template x-if="uform">) are never disposed when the x-if tears the modal down, so each open registers another 8 live effects that all re-run and throw when uform is set back to null. The screen stays usable (the users table still rendered 3 rows), but the leak is unbounded on a page an owner may keep open all day.
- **Expected:** No uncaught exceptions. The grant-checkbox effects should be disposed along with the modal, so closing the dialog N times throws 0 errors regardless of N and registers no new effects.
- **Evidence:** cycle 1: 0  cycle 2: 8  cycle 3: 16  cycle 4: 24  cycle 5: 32  cycle 6: 40  cycle 7: 48  cycle 8: 56 (running total 224); sample error: "Cannot read properties of null (reading 'grants')"; page still functional? users rows = 3

### [MINOR] Usernames are case-sensitive, so 'Admin' can be created alongside the existing 'admin'
- **Steps:** 1. Sign in as admin/admin123.
2. Click "Users & Access", then "+ Add account".
3. Enter Username = `Admin` (capital A) and Password = `secret123`, leave it as a Staff account, click Save.
4. Look at the account list.
5. Delete the 'Admin' row to clean up.
Scripted equivalent: POST /api/users {"username":"Admin","password":"secret123","role":"operator","grants":[]}
- **Observed:** The create succeeds with 200 and the account list now contains both 'admin' and 'Admin' as two separate accounts with separate passwords and separate grants. The duplicate check in create_user() uses a plain SQL `WHERE username=?`, which is case-sensitive in SQLite, so only an exact-case match is rejected (`  admin  ` is correctly caught because it is stripped first, but `Admin` is not). Login is case-sensitive too, so the two accounts are genuinely distinct and an owner can easily end up with a shadow copy of a real user's account.
- **Expected:** A case-insensitive duplicate check, rejecting 'Admin' with the same 400 "'admin' already exists" that an exact duplicate gets.
- **Evidence:** create 'Admin' while 'admin' exists -> 200; usernames: ['admin', 'operator', 'ztest-shell-user', 'Admin']

### [COSMETIC] The password field keeps its contents after a failed sign-in
- **Steps:** 1. Open http://127.0.0.1:8010/.
2. Enter username `admin`, password `wrongpass`, click "Sign in".
3. Read back the value of the password input after the error appears.
- **Observed:** The password input still contains `wrongpass` after the failed attempt (doLogin() in shell.js only clears login.password on the success path), so the user has to select and delete it before retyping.
- **Expected:** The password field should be cleared on a failed sign-in, as it already is on a successful one.
- **Evidence:** pw field after fail: 'wrongpass'

## Salary & Attendance

### [MAJOR] 'Edit pay' in Pay Setup does nothing - the modal opens and closes itself within the same click
- **Steps:** 1. Open http://127.0.0.1:8010/payroll/ as admin. 2. Click 'Pay Setup' in the left nav. 3. Click 'Edit pay' on any employee row (mouse click or focus + Enter both reproduce). Nothing appears. To see what happens under the hood, before clicking run this in the console: window.__m=[];new MutationObserver(ms=>{for(const m of ms){for(const n of m.addedNodes) if(n.nodeType===1&&String(n.className||'').includes('fixed')) window.__m.push('ADDED'); for(const n of m.removedNodes) if(n.nodeType===1&&String(n.className||'').includes('fixed')) window.__m.push('REMOVED');}}).observe(document.body,{childList:true,subtree:true}); then click 'Edit pay' and read window.__m. Calling document.body._x_dataStack[0].editEmployee(s.employees[0]) from the console (no click involved) DOES open and keep the modal, which isolates it to the click.
- **Observed:** window.__m === ['ADDED','REMOVED'] and document.body._x_dataStack[0].empForm === null; the pay-setup modal never stays on screen (0 matches for div.fixed.inset-0:has-text('Pay setup')). The modal markup is <template x-if="empForm"> whose panel carries @click.outside="empForm=null": the modal is inserted during the microtask checkpoint of the button's own click handler, so the freshly-registered document-level click-outside listener fires for that same still-propagating click and immediately clears empForm. Net effect: base salary, PF/ESI flags and the advance balance cannot be edited anywhere in the Salary module. (The employee-name button next to it, which opens the profile modal via an async fetch, works fine.)
- **Expected:** Clicking 'Edit pay' opens the pay-setup modal and keeps it open until Cancel/Save or a genuine outside click.
- **Evidence:** MutationObserver log ['ADDED','REMOVED'] on click; empForm null after both mouse click and keyboard Enter; no pageerror, no console error, no HTTP request.

### [MAJOR] Salary screen accepts paid days far outside the month, computing (and offering to publish) absurd or negative pay with no warning
- **Steps:** 1. /payroll/ as admin -> 'Calculate Salary'. 2. Set Period to June 2026 and click 'Prepare'. 3. In any row (I used Sandeep, employee 7, base 17600, 30-day month) type 999 into the amber 'Days' column. 4. Click 'Calculate'. 5. Repeat with -10 in the same cell. STOP at Calculate - do not press Publish, it would overwrite the published 2026-06 run.
- **Observed:** Days=999 -> Att% column shows 3330.0, base_att 586,080, Total 586,609 for an employee whose monthly base is 17,600. Days=-10 -> Att% -33.3, base_att -5,866.67, Total -5,337 and Cash -5,337 (a negative payslip). In both cases row.error stays null, the row is NOT highlighted, and s.payErrors().length === 0, i.e. nothing at all stands between this figure and the Publish button (the only publish guards are the advance error and a cheque+cash mismatch).
- **Expected:** Paid days should be clamped/validated to 0..total_days (or at minimum the row should be flagged and Publish blocked) so a mistyped day count cannot produce a 33x overpayment or a negative salary.
- **Evidence:** attendance_percentage 3329.9999999999995, base_att 586080, total 586609, error null, payErrors 0; and attendance_percentage -33.33333333333333, base_att -5866.666666666665, total -5337, cash -5337, error null.

### [MAJOR] A fractional value in Cheque aborts the entire payroll publish with a message that contradicts the figures shown
- **Steps:** 1. /payroll/ as admin -> 'Calculate Salary' -> Period June 2026 -> 'Prepare'. 2. Type 100.5 into any row's 'Cheque' cell (I used Sandeep / employee 7). 3. Click 'Calculate' - the row shows Total 12423, Cheque 100.5, Cash 12322.5 and is NOT highlighted red. 4. Click 'Publish'.
- **Observed:** POST /api/payroll/publish -> HTTP 400 with toast 'Sandeep: Cheque + Cash must equal Total', even though the screen shows 100.5 + 12322.5 = 12423 exactly. The whole 79-employee run is rejected (0 rows written) because the backend truncates with int(cheque)/int(cash) before comparing (100 + 12322 = 12422 != 12423), while the client-side payErrors() check uses floats and reports 0 errors. Nothing on screen points at the offending cell or explains that decimals are not allowed.
- **Expected:** Either reject/round the fractional cheque at entry time (flag the row like the other errors do) or handle it consistently server-side. A publish must not fail with a message the displayed numbers contradict.
- **Evidence:** UI row after Calculate: {total: 12423, cheque: 100.5, cash: 12322.5, error: null}, s.payErrors().length === 0; then (400, POST, /api/payroll/publish), toast 'Sandeep: Cheque + Cash must equal Total'; GET /api/pay?period=2026-06 returned 0 rows afterwards (nothing partially committed).

### [MINOR] CEO attendance adjustment saves out-of-range metrics (negative paid days, paid days above the month length, negative overtime)
- **Steps:** 1. /payroll/ as admin -> Attendance -> 'Single employee'. 2. Pick an employee with a saved summary (employee 7 / period 2026-06 has one) and click 'Load Days'. 3. In the 'This month - CEO adjustments' panel set 'Paid days' to -5 and click 'Save adjustments'. 4. Repeat with 999, and with 'Overtime hrs' = -3. 5. Restore sane values afterwards (I restored 23 / 2 / 7 / 2).
- **Observed:** Every value is accepted: POST /api/attendance/7/2026-06/override -> 200 with toast 'Attendance metrics adjusted'. Stored summary became present_days -5 with attendance_percentage -16.666666666666664, then present_days 999 with attendance_percentage 3329.9999999999995 on a 30-day month, and total_overtime_hours -3. These values are what /api/payroll/prepare feeds into the salary table, so they propagate straight into pay.
- **Expected:** present_days should be constrained to 0..total_days and overtime hours to >= 0, or at least warned about, since the summary is the input to the payslip.
- **Evidence:** Three consecutive 200 responses on /api/attendance/7/2026-06/override; resulting summaries {present_days: -5, attendance_percentage: -16.666666666666664}, {present_days: 999, attendance_percentage: 3329.9999999999995}, {total_overtime_hours: -3}.

### [MINOR] Clearing the Period on Export Slips navigates the whole app away to a raw JSON 404 page
- **Steps:** 1. /payroll/ as admin -> 'Export Slips'. 2. Click the month input, select all (Ctrl+A) and press Delete so the field is empty. 3. Click 'CEO Sheet' (same for 'Distribution Slip').
- **Observed:** The browser navigates to http://127.0.0.1:8010/api/export/ceo/ and the page body becomes {"detail":"Not Found"} (HTTP 404). The application UI is gone; the user has to press Back. download(kind) does window.location = `/api/export/${kind}/${exp.period}` with no guard on an empty period.
- **Expected:** An empty period should show an inline validation message / error toast and keep the user in the app.
- **Evidence:** pg.url after the click: http://127.0.0.1:8010/api/export/ceo/ ; body text {"detail":"Not Found"} ; response (404, GET, /api/export/ceo/).

### [MINOR] Clearing the Period in View Salaries silently loads every published month at once
- **Steps:** 1. /payroll/ as admin -> 'View Salaries'. 2. Clear the month input (click, Ctrl+A, Delete). 3. Click 'Load'.
- **Observed:** GET /api/pay?period= returns 200 and the table filled with 149 rows spanning both 2026-06 and 2026-07 mixed together, with no column or heading indicating which month each row belongs to (the period field is not displayed). A CEO reading totals off this screen would double-count.
- **Expected:** An empty period should either be rejected with a message or the table should show the period per row.
- **Evidence:** s.history.rows.length === 149, distinct periods ['2026-06','2026-07'], request (200, GET, /api/pay?period=).

### [MINOR] Malformed period in the URL returns HTTP 500 instead of a 4xx
- **Steps:** From the signed-in browser console (or any authenticated client): fetch('/api/payroll/prepare/2026-13'), fetch('/api/payroll/prepare/abcd'), fetch('/api/export/ceo/2026-13'), fetch('/api/export/ceo/abcd').
- **Observed:** All four return HTTP 500 'Internal Server Error' (the period string is split/int-parsed and handed to calendar.monthrange without validation). Not reachable through the month picker, but it is an unguarded 500 on public-facing admin routes.
- **Expected:** HTTP 400/422 with a 'bad period' message.
- **Evidence:** prepare '2026-13' -> 500 Internal Server Error; prepare 'abcd' -> 500; export ceo '2026-13' -> 500; export ceo 'abcd' -> 500.

### [MINOR] Advance accepts negative cheque/cash components as long as they sum to the amount
- **Steps:** 1. /payroll/ as admin -> 'Advances'. 2. Pick an employee, Amount = 100, Cheque = -50, Cash = 150. 3. Click 'Issue'. 4. Click 'Refresh' on the Ledger panel.
- **Observed:** POST /api/advances -> 200 'Advance issued'. The stored row is {amount: 100, cheque: -50, cash: 150} and the employee's outstanding advance goes up by 100. Only the total is validated (amount > 0 and cheque + cash == amount); the individual components are not.
- **Expected:** Cheque and cash components should be rejected when negative.
- **Evidence:** Ledger row after issue: {"id": 4, "employee_id": 7, "amount": 100, "txn_date": "2026-08-14", "type": "CR", "cheque": -50, "cash": 150}.

### [COSMETIC] Cosmetic: an empty month gives the bare toast 'Not Found'
- **Steps:** /payroll/ -> 'Calculate Salary' (or Attendance -> 'Load month'), clear the month input, click Prepare / Load month.
- **Observed:** Toast reads only 'Not Found' (the raw FastAPI 404 detail from GET /api/payroll/prepare/ and GET /api/attendance-grid/); nothing tells the user the month is missing.
- **Expected:** A message like 'Pick a month first'.
- **Evidence:** toast {'msg': 'Not Found', 'kind': 'err'} after (404, GET, /api/payroll/prepare/) and (404, GET, /api/attendance-grid/).

## Order Tracking

### [MAJOR] Double-clicking "Save order" creates two complete duplicate orders (two order numbers burned)
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010 and open http://127.0.0.1:8010/orders/. 2. Click "+ New order". 3. Customer = Bharat Castings, Customer PO no. = ztest-orders-DBLCLICK, item 1 Description = "ztest-orders double submit probe", Qty = 1, rate = 10. 4. Double-click the "Save order" button (a normal user double-click; in Playwright pg.dblclick("button[type=submit]:has-text('Save order')")). 5. Wait ~2s, then search the orders list for "ztest-orders-DBLCLICK".
- **Observed:** Two POST requests are sent to /api/orders and TWO orders are created: ORD-26-27-017 (id 21) and ORD-26-27-018 (id 22), both with customer_po "ztest-orders-DBLCLICK" and the identical single item. Both survive a reload. The Save button is never disabled while the request is in flight and saveOrder() has no in-flight guard, so the second click re-submits the still-populated form.
- **Expected:** One click's worth of work: a single order should be created (the submit button should be disabled / the second submit ignored while the first POST is in flight). Two financial-year sequence numbers are consumed and the shop now has a phantom duplicate order.
- **Evidence:** Script output: `POSTs fired on a double-click: 2` then `orders now carrying that PO: [[22, 'ORD-26-27-018', 'ztest-orders-DBLCLICK'], [21, 'ORD-26-27-017', 'ztest-orders-DBLCLICK']]`. No 4xx/5xx — both POSTs returned 200.

### [MAJOR] Double-clicking "Create consignment" creates two identical consignments and doubles the dispatched quantity
- **Steps:** 1. Sign in as admin, open /orders/. 2. "+ New order" → customer Bharat Castings, PO "ztest-orders-DBLSHIP", item Description "ztest-orders dbl-ship part", Qty 10, rate 100 → Save order. (This produced ORD-26-27-020, id 24, order_item id 30.) 3. On the order detail click "🚚 Ship". 4. Set the line quantity to 2 (of 10 pending) and LR number to "ztest-LR-DBLSHIP". 5. Double-click "Create consignment". 6. Reopen the order (s.open(24) or click the row).
- **Observed:** Two POSTs to /api/orders/consignments both succeed. Two consignments are created — id 11 and id 12, both dated 2026-08-14, both LR "ztest-LR-DBLSHIP", both created_at 2026-08-14T15:15:01 — and the order item shows shipped = 4 of 10 instead of 2. Two LR/e-way/invoice documents now exist for one physical dispatch and the shipped quantity is double what the user entered. (When the entered quantity happens to equal the whole pending amount the second POST is correctly refused with "Only 0 of 2 left to ship on that item", so the over-ship guard itself is fine — the bug is the un-guarded double submit.)
- **Expected:** One consignment with qty 2, shipped = 2. The submit button should be disabled while the POST is in flight.
- **Evidence:** `POSTs: 2 | consError: ''` … `items: [(30, 10, 4)]` … `consignments: [[12, 'ztest-LR-DBLSHIP', '2026-08-14T15:15:01'], [11, 'ztest-LR-DBLSHIP', '2026-08-14T15:15:01']]`

### [MINOR] Closing the order detail throws 7 uncaught TypeErrors, and 7 more for every order opened since (unbounded effect leak)
- **Steps:** 1. Sign in as admin and open http://127.0.0.1:8010/orders/ in a FRESH browser context with a pageerror listener attached before navigation. 2. Click any order row → the detail panel opens. 3. Click the ✕ button in the detail header (the one bound to closeDetail()). 4. Click a row again → detail opens. 5. Click ✕ again. 6. Repeat steps 4-5 three more times.
- **Observed:** The 1st close is clean; the 2nd close raises 7 uncaught "TypeError: Cannot read properties of null (reading 'stage')"; the 3rd raises 14, the 4th 21, the 5th 28 — 70 uncaught exceptions after five open/close cycles. The count is exactly 7 (the number of stage chips) per order-detail that was ever opened, i.e. the `:class` effects on the stage-progress buttons in frontend/orders/index.html (`detail.stages.findIndex(x => x.key === detail.stage)`) are never disposed when x-if tears the panel down, so they all re-run against `detail === null`. The panel itself still closes and re-opens correctly and the stage chips still render and work, so there is no immediate functional breakage — but the number of dead throwing effects grows without bound for the life of the page.
- **Expected:** Closing the detail panel should not raise any uncaught exception, and the effect count should not grow with each open.
- **Evidence:** cycle 0: open +0, close +0 / cycle 1: close +7 / cycle 2: close +14 / cycle 3: close +21 / cycle 4: close +28; `total uncaught errors: 70`, `distinct: {"Cannot read properties of null (reading 'stage')"}`. Screenshot orders-detail-after-errors.jpg shows the panel still rendering fine afterwards.

### [MINOR] Clearing the Order date while editing silently rewrites it to today, leaving the FY-stamped order number wrong
- **Steps:** 1. Sign in as admin, open /orders/. 2. Create (or find) an order whose order date is in a previous financial year — I used ORD-25-26-002 (id 14), order_date 2026-03-31, created via "+ New order" with Order date set to 2026-03-31. 3. Open it and click "Edit". 4. Clear the "Order date" field (leave it empty) and change nothing else. 5. Click "Save order". 6. Reload the page and reopen the order.
- **Observed:** The save succeeds with no error, no warning and no confirmation: order_date changes from 2026-03-31 to 2026-08-14 (today) and persists across a reload. The order number stays ORD-25-26-002, so the record now claims financial year 25-26 while its order date sits in FY 26-27 — the number and the date disagree, and the original order date is gone with no way to know it was overwritten. (Backend `_check_date(..., required=True)` silently substitutes date.today() for an empty value on UPDATE as well as on CREATE.)
- **Expected:** Either reject the empty order date on an existing order ("Order date is required") or leave the stored date untouched — an existing record's date should not be silently replaced with today.
- **Evidence:** before edit: ['ORD-25-26-002', '2026-03-31'] → formError: '' | form closed: True → after edit: ['ORD-25-26-002', '2026-08-14'] → after reload: ['ORD-25-26-002', '2026-08-14']

### [COSMETIC] Order list shows an unrounded rupee amount (₹3,280.875) where the order detail shows ₹3,280.88
- **Steps:** 1. Sign in as admin, open /orders/. 2. Create an order with an item that produces a fractional paisa: Qty 3.5, rate 80.25 (I used ORD-26-27-006, PO ztest-orders-PO-2, plus a second item 12 x 250). 3. Look at the Amount column in the orders list, then open the order and look at "Items · total".
- **Observed:** The list row renders "₹3,280.875" (three decimals on a rupee figure) because list_orders returns SUM(qty*rate) unrounded, while the detail panel renders "₹3,280.88" because get_order rounds each line to 2 dp. Same order, two different totals on two screens.
- **Expected:** Both screens should show the same 2-decimal rupee amount.
- **Evidence:** list row cells: ['ORD-26-27-006', '14 Aug 2026', 'Sterling Valves', 'ztest-orders-PO-2', '2', '₹3,280.875', ...]  vs  detail amount: 3280.88, items: [[12, 250, 3000], [3.5, 80.25, 280.88]]

## Quotations & Invoices

### [MAJOR] Printable invoice/quotation mangles quantities through %g formatting (12345.67 prints as 12345.7; 1200000 prints as 1.2e+06)
- **Steps:** 1. Sign in at http://127.0.0.1:8010 as admin/admin123 and open /quotations/. 2. Click '+ Invoice'. 3. Customer = Bharat Hydraulics, GST% = 18. 4. Line 1: description 'zqi-print big qty steel bar', qty 12345.67, unit kg, rate 85.5. 5. Add line 2: description 'zqi-print million pieces washer', qty 1200000, unit Nos, rate 0.75. 6. Save (this produced INV-26-27-006, id 13). 7. Click 'Print / Save as PDF' in the detail modal (or open http://127.0.0.1:8010/api/quotations/13/print).
- **Observed:** The QTY column of the printed TAX INVOICE reads '12345.7' for line 1 and '1.2e+06' for line 2, while the AMOUNT column is computed from the true quantities (10,55,554.78 and 9,00,000.00). The printed document is internally inconsistent: 12345.7 kg x 85.50 = 10,55,557.35, not the 10,55,554.78 printed next to it. The detail modal and the API show the correct 12345.67 and 1200000.
- **Expected:** The printed quantity should be the stored quantity — '12345.67' and '12,00,000' (or at least '1200000'). Scientific notation must never appear on a customer-facing tax document.
- **Evidence:** Print row text: '1\tzqi-print big qty steel bar\t12345.7\tkg\t85.50\t10,55,554.78' and '2\tzqi-print million pieces washer\t1.2e+06\tNos\t0.75\t9,00,000.00'. API detail for the same doc: lines qty 12345.67 and 1200000. Screenshot: tour/quotations-print-bigqty.jpg

### [MAJOR] Printed TAX INVOICE has no supplier (company) address or GSTIN, and nothing in the app can set one
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Open any invoice (e.g. INV-26-27-005) and click 'Print / Save as PDF'. 3. Look at the header block under 'APEX THERMOCON'. 4. Go to /settings/ and look for any company address / GSTIN field.
- **Observed:** The company block renders as an empty '<div class="addr"></div>' — the printed tax invoice shows only the words 'APEX THERMOCON' with no address and no seller GSTIN, while the buyer's full address and GSTIN print correctly right below it. The print template reads the settings keys 'company_address' and 'company_gstin', but no route or UI field anywhere in the app writes those keys (settings only exposes order-format, units, operations, departments), so the block is permanently blank.
- **Expected:** Either the print view should render a configured company address and GSTIN, or Settings should expose fields to enter them. A document headed 'TAX INVOICE' cannot go out without the supplier's address and GSTIN.
- **Evidence:** Regex over the print HTML: addr div content = [''] (empty). Printed text jumps straight from 'APEX THERMOCON' to 'TAX INVOICE'. `grep -rn 'company_address' backend frontend config` matches only backend/modules/quotations.py:308-309 (the reader); there is no writer. Screenshot: tour/quotations-print-invoice.jpg

### [MAJOR] Edit form shows the saved Customer as '—' and every part line as '(no drawing)' even though the document has them
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Click the row INV-26-27-005 (an invoice raised from ORD-26-27-001, whose first two lines are linked to drawings DRG-4711 rev A and rev B). 3. Click 'Edit'. 4. Look at the 'Customer *' dropdown and at each line's part dropdown.
- **Observed:** The Customer dropdown displays '—' (selectedIndex 0, DOM value '') and all three line dropdowns display '(no drawing)' (DOM value ''), even though the Alpine state is correct (form.customer_id === 1, lines drawing_id [1, 2, '']) and the customer <select> already contains all 22 options. The underlying data survives a blind Save, but the required Customer field looks empty and the drawing links look lost. The same selects render correctly when the state is changed after the form is already open (prefill-from-order shows 'KP01 · Krishna Pumps Pvt Ltd' and 'KP-2208 rev A'), so this is specific to opening the Edit form.
- **Expected:** Opening Edit should show the document's saved customer and each line's saved part selected in the dropdowns.
- **Evidence:** DOM probe on the open Edit form: {state_customer_id: 1, cust_dom_value: "", cust_selected_text: "—", cust_option_count: 22, state_line_drawings: [1,2,""], draw_dom_values: ["","",""], draw_selected_text: ["(no drawing)","(no drawing)","(no drawing)"]}. Control case after prefill: {cust_v: "2", cust_txt: "KP01 · Krishna Pumps Pvt Ltd", draw_v: ["3"], draw_txt: ["KP-2208 rev A"]}. Screenshots: tour/quotations-edit-form-blank-selects.jpg vs tour/quotations-prefill-selects.jpg. Side effect: a user who 'fixes' the blank part dropdown by re-picking the drawing triggers linePicked(), which silently overwrites that line's description and rate with the drawing's latest rate.

### [MAJOR] An invoice can be billed to one customer while linked to a different customer's order — the printed invoice then shows the other customer's order number
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Click '+ Invoice'. 3. In 'Start from an order (fills customer + lines)' pick 'ORD-26-27-001 · Bharat Hydraulics' — the form fills with customer_id 1 and order_id 1. 4. Now change the 'Customer *' dropdown to 'SV01 · Sterling Valves' (customer 3). 5. Type a reference and click Save.
- **Observed:** The document saves without any warning as INV-26-27-007, customer 'Sterling Valves', order 'ORD-26-27-001' (which belongs to Bharat Hydraulics). Its printable TAX INVOICE reads 'BILLED TO Sterling Valves (SV01) … Against order ORD-26-27-001'. The backend's _check_refs only verifies that the order exists, never that it belongs to the invoiced customer, and the frontend never clears order_id when the customer changes.
- **Expected:** Either the save should be rejected ('that order belongs to another customer'), or changing the customer should clear the order link. A customer-facing invoice must not carry another customer's order number.
- **Evidence:** Save returned err='' with detail {doc_no: 'INV-26-27-007', customer_name: 'Sterling Valves', order_no: 'ORD-26-27-001'}. Print output: 'BILLED TO / Sterling Valves (SV01) / Plot 7, MIDC Bhosari … / Against order\tORD-26-27-001'. Screenshot: tour/quotations-print-crosscustomer.jpg

### [MINOR] List 'Total' column disagrees with the detail modal and the printed document by a paisa (different rounding of the same lines)
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Click '+ Quotation'. 3. Customer = Krishna Pumps Pvt Ltd, GST% = 18. 4. Add three identical lines, each: description 'zqi-round line N', qty 1.15, unit Nos, rate 1010.15. 5. Save (this produced QUO-26-27-008). 6. Close the detail modal and read the Total column of the QUO-26-27-008 row in the list. 7. Click the row and read the Total in the detail modal, then open its printed copy.
- **Observed:** List row shows ₹4,112.32. Detail modal shows Subtotal ₹3,485.01, GST ₹627.30, Total ₹4,112.31, and the printed copy shows the same 3,485.01 / 627.30 / 4,112.31. The list computes SUM(qty*rate) unrounded (35.0175-style values -> subtotal 3485.02) while the detail sums the per-line rounded amounts (3 x 1161.67 = 3485.01).
- **Expected:** One document, one total — the list, the detail and the printed copy should agree to the paisa.
- **Evidence:** List API row for id 14: {subtotal: 3485.02, tax: 627.3, total: 4112.32}; detail API for id 14: {subtotal: 3485.01, tax: 627.3, total: 4112.31}; DOM cell text '₹4,112.32' vs modal 'Total ₹4,112.31'. Screenshot: tour/quotations-rounding-detail-vs-list.jpg

### [MINOR] Opening and closing a document leaks Alpine effects: every close throws a growing pile of uncaught TypeErrors
- **Steps:** 1. Sign in as admin, attach a pageerror listener, open /quotations/. 2. Click a document row (e.g. QUO-26-27-002) to open the detail modal, then click the ✕ button to close it. 3. Repeat step 2 five more times, counting uncaught page errors after each close.
- **Observed:** Uncaught 'TypeError: Cannot read properties of null (reading 'status')' are thrown on close, and the count grows linearly with how many times the modal has been opened: 0, 5, 10, 15, 20, 25 on closes 1-6 (60 errors over 3 cycles in another run). The failing expression is the status-button loop in frontend/quotations/index.html:151, `s === detail.status ? …`, still evaluating after closeDetail() sets detail = null — i.e. each open registers 5 more effects (one per status) that are never disposed. The modal does close and the page keeps working, but the leak grows for the life of the page.
- **Expected:** Closing the detail modal should tear its bindings down cleanly; no uncaught exceptions, and no unbounded growth in live effects.
- **Evidence:** Timing run: 'delay=0ms -> 0, delay=100ms -> 5, delay=250ms -> 10, delay=500ms -> 15, delay=1000ms -> 20, delay=2000ms -> 25' uncaught errors per close cycle. Sample stack: 'at [Alpine] s === detail.status ? \'bg-brand-600 text-white border-brand-600\' : … at alpine.js:5:1068'.

### [MINOR] 'Edit' button is live on paid/cancelled documents but the form can never be saved
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Click a document you own (e.g. QUO-26-27-008). 3. Click the 'paid' status button and wait for the badge to change. 4. Click 'Edit' — the edit form opens normally. 5. Change any quantity and click Save.
- **Observed:** The edit form opens fully editable, and only on Save does the server refuse with 'A paid document can't be edited'; the form stays open with the user's edits stranded. Same for a cancelled document.
- **Expected:** Once a document is paid or cancelled, the Edit button should be hidden/disabled (or the form should warn on open) rather than letting the user retype changes that can never be saved.
- **Evidence:** After marking paid: s.detail.status === 'paid'; clicking Edit gives s.form !== null; after Save, s.formError === "A paid document can't be edited" and s.form is still non-null. Screenshot: tour/quotations-paid-edit-blocked.jpg

### [MINOR] GST % accepts absurd rates (200% saved and printed)
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Click '+ Quotation'. 3. Customer = Bharat Hydraulics, GST % = 200. 4. Add one line: description 'zqi-edge tax200', qty 1, unit Nos, rate 100. 5. Save (this produced QUO-26-27-007) and open its printed copy.
- **Observed:** Saved without complaint: subtotal 100, tax 200, total 300; the printed document shows a 'GST @ 200%' line of 200.00 on a 100.00 subtotal. The backend only bounds tax_pct at 0 <= x <= 1e12.
- **Expected:** The GST percentage should be bounded to a sane range (0-100, realistically the GST slabs) so a typo like 180 instead of 18 is caught before it reaches a customer-facing document.
- **Evidence:** Detail for QUO-26-27-007 (id 12): {subtotal: 100, tax: 200, total: 300, tax_pct: 200}.

### [COSMETIC] Printed lines omit the drawing revision, so two different revisions of the same drawing are indistinguishable
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Open INV-26-27-005 (raised from ORD-26-27-001; line 1 is DRG-4711 rev A, line 2 is DRG-4711 rev B). 3. Compare the detail modal's part column with the printed copy.
- **Observed:** The detail modal shows 'DRG-4711 rev A' and 'DRG-4711 rev B', but the printed invoice shows both lines as bold 'DRG-4711' — the revision is fetched by the query but never rendered, so the customer sees two identical part numbers at different rates.
- **Expected:** The printed part heading should include the revision, as the on-screen detail and the drawing picker both do.
- **Evidence:** Print rows: '1\tDRG-4711 / Hydraulic piston rod O25 … 270.00' and '2\tDRG-4711 / Rev B batch … 298.00'. Screenshot: tour/quotations-print-invoice.jpg

### [COSMETIC] Type-tab counters ignore the search box, and one validation message has a doubled space
- **Steps:** 1. Sign in as admin and open /quotations/. 2. Type 'zqi-crosscust' in the Search box and wait for the list to filter. 3. Read the 'All (…)' / 'Quotations N' / 'Invoices N' tab labels. Separately: open '+ Quotation', pick a customer, add a line with qty 0 and Save.
- **Observed:** With the search returning a single row, the tabs still read 'All (18)', 'Quotations 11', 'Invoices 7' — the counts come from an unfiltered COUNT(*) query. The qty-0 error reads 'Line 1 quantity must be a normal  positive number' with two spaces between 'normal' and 'positive'.
- **Expected:** Counters should reflect the current filter (or be labelled as totals); the message should read 'must be a normal positive number'.
- **Evidence:** s.data.rows = ['INV-26-27-007'] while s.data.counts = {quotation: 11, invoice: 7} and the tab text was 'All (18)'. formError string: 'Line 1 quantity must be a normal  positive number'.

## Settings

### [MAJOR] "Add" with an existing operation name silently overwrites that operation's standard hourly rate, with no warning and no feedback
- **Steps:** 1. Sign in as admin, open http://127.0.0.1:8010/settings/. 2. In 'Machining operations & hourly rates', bottom form: type 'ztest-settings-dup' in 'New operation...', '400' in the '/hr' box, click Add. A row appears with rate 400 (operation count 21 -> 22). 3. In the SAME bottom form type the identical name 'ztest-settings-dup' again, put '50' in the rate box, click Add. 4. Reload the page.
- **Observed:** No error, no confirmation dialog, no toast of any kind. The operation count stays 22 (no new row), and the EXISTING operation's rate silently changes from 400 to 50; after reload it is still 50. The server does INSERT ... ON CONFLICT(name) DO UPDATE SET rate_per_hour. The same key press sequence against a real seeded name (e.g. typing 'Turning' into the Add box) would silently rewrite that operation's standard /hour rate, and every costing built afterwards prices at the new rate.
- **Expected:** An 'Add' action must not mutate an existing record. Either reject the duplicate name ('Turning already exists - edit its rate in the table above') or ask for explicit confirmation before changing a live pricing rate; at minimum show a toast saying which existing rate was changed.
- **Evidence:** A1 created: 400 | ops count: 22 ; A2 after re-adding the SAME name with 50 -> rate: 50 | ops count: 22 | toast: {"show":false,"msg":"","kind":"ok"} ; A3 persisted rate after reload: 50 (HTTP 200 on POST /api/settings/operations)

### [MINOR] Clearing an operation's rate box and clicking Save stores 0/hour and reports success
- **Steps:** 1. Sign in as admin, open /settings/. 2. In the operations table pick a row (I used my own 'ztest-settings-dup', first set to 425 and saved). 3. Click into that row's Rate (/hour) box, select all and delete so the box is empty. 4. Click that row's 'Save'. 5. Reload the page. (Same result if you type letters into the box: the number input discards them, leaving it empty - typing 'abc' then Save also stored 0.)
- **Observed:** Green toast 'ztest-settings-dup rate saved' and the stored rate becomes 0; after reload the rate is still 0. The client sends Number(value) || 0, so an empty required field is silently converted to zero. Any costing created afterwards prices that operation at 0/hour.
- **Expected:** An empty rate box is not a rate of zero. The save should be refused with an error ('Enter a rate'), or the previous value kept; if 0 really is intended it should at least be stated in the confirmation.
- **Evidence:** B2 dom value after clearing: '' ; B3 toast: {"show":true,"msg":"ztest-settings-dup rate saved","kind":"ok"} | rate now: 0 ; B4 persisted after reload: 0

### [COSMETIC] No feedback at all when an Add is a no-op
- **Steps:** 1. /settings/ as admin. 2. Add unit 'Nos' (already present) - or re-add an existing department. 3. Click Add in the operations form with the name box empty but a rate filled in.
- **Observed:** Nothing happens and nothing is said: the field is simply cleared (units/departments) or the click is swallowed (empty operation name). The user cannot tell whether the item was added, was already there, or the button is broken.
- **Expected:** A short toast such as 'Nos is already in the list' / 'Enter an operation name'.
- **Evidence:** [exact-dup] add='ztest-settings-unit' count 50->50 toast={"show":false,...}; [add empty-name] name='' rate='50' count 22->22 toast={"show":false,...} - no HTTP request is made

### [COSMETIC] Unit and department names have no length limit
- **Steps:** 1. /settings/ as admin. 2. In 'Add a unit...' paste a 320-character string (I used 'ztest-settings-long-' + 300 x 'L') and press Enter. 3. Open /parts/ and look at the unit dropdown data (/api/parts/refs).
- **Observed:** The 320-character unit is accepted, stored, rendered as one enormous chip in the Units panel and propagated into the unit datalist used by Parts and Orders. The order-number format is capped at 40 chars but unit/department names are uncapped.
- **Expected:** A sane maximum length (and the same for departments), consistent with the format field's 40-char cap.
- **Evidence:** [very-long] count 51->52 present=True; parts lists.units contains 'ztest-settings-long-LLLL...' (320 chars)

## Employee Management

### [MINOR] Out-of-range integers reach SQLite unguarded — creating an employee with a very large base salary returns HTTP 500 'Internal Server Error'
- **Steps:** 1. Sign in at http://127.0.0.1:8010 as admin/admin123 and open /employees/.
2. Click '+ Add employee'.
3. Full name: 'ztest-employees-500demo'. Leave Department/Shift/Date joined at their defaults.
4. Click into 'Base salary (₹/month)' and type 99999999999999999999 (twenty 9s).
5. Click Save.
The same class of failure is reachable from the leave bank: open any non-overtime employee, type 99999999999999999999 into the leave-days box next to the +/- buttons and click '+' (POST /api/employees/{id}/leave-adjust).
- **Observed:** POST /api/employees returns HTTP 500 and the form shows a red 'Internal Server Error' under the fields (screenshot employees-500-huge-salary.jpg). The server log shows an unhandled `OverflowError: Python int too large to convert to SQLite INTEGER` raised from backend/modules/employees/repo.py line 60 (create_employee) — no 400 handling. The leave-bank variant produces the same 500 from repo.py line 253 (adjust_leave) and the toast reads 'Internal Server Error'. The boundary is low: 9223372036854775807 (a valid int64) also 500s, because JSON.stringify of the JS Number rounds it up past 2^63-1. 1000000000000000000 is the largest value I got through.
- **Expected:** A validation error such as 'Base salary is too large' (HTTP 400/422) shown in the form, not an unhandled 500 with a generic 'Internal Server Error' message.
- **Evidence:** Browser network: POST http://127.0.0.1:8010/api/employees -> 500; POST http://127.0.0.1:8010/api/employees/71/leave-adjust -> 500. Server log (scratchpad/server8010.log lines 1141, 1303, 1559, 1640, 1721 and 1981): 'OverflowError: Python int too large to convert to SQLite INTEGER' inside employees/repo.py create_employee and adjust_leave. Alpine state after the click: s.formError === 'Internal Server Error', s.form still non-null, roster row count unchanged.

### [MINOR] No upper bound on base salary — a ₹1,000,000,000,000,000,000 monthly salary is accepted and stored
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010 and open /employees/.
2. Click '+ Add employee'.
3. Full name: 'ztest-employees-bigpay'. Base salary: 1000000000000000000 (1 followed by 18 zeros).
4. Click Save.
5. Open the new row and look at the 'Pay' panel on the left of the detail drawer.
- **Observed:** The employee is created with no warning ('... saved' toast) and the record stores base_salary = 1000000000000000000. The detail drawer's Pay panel renders it as '₹1,00,00,00,00,00,00,00,00,000'. I created this record as id 78 (since reset to 10000 and deactivated during cleanup).
- **Expected:** A sanity ceiling on monthly base salary, rejected with a validation message the same way a blank or zero salary already is. Right now the only thing that stops an absurd figure is the int64 overflow one digit later, which 500s instead of validating.
- **Evidence:** Row after save: {"id": 78, "name": "ztest-employees-b19", "dept": "CNC", "base_salary": 1000000000000000000, "leave_balance": 4, "active": 1}. Alpine state s.formError was '' and s.form became null (form closed = success).

### [MINOR] Edit form's Department dropdown shows the wrong department when the employee's department is no longer in the configured list
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010, open /employees/, open any employee's detail drawer (I used the record I created, id 71).
2. Put the employee into a department that is not in the configured departments list. I did this from the page's own console/fetch:
   await fetch('/api/employees/71', {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:'ztest-employees-alpha', dept:'ztest-GhostDept', shift:'D', overtime_eligible:false, date_joined:'2025-06-30'})})
   (The product route to the same state is Settings -> Departments -> delete a department that employees are still assigned to; nothing reassigns those employees.)
3. Reload /employees/, open that employee. The drawer header correctly shows 'ztest-GhostDept'.
4. Click 'Edit' and look at the Department <select>.
- **Observed:** The Department select displays 'CNC' — the first option in the list — while the record's department is 'ztest-GhostDept' and the Alpine model behind the select (s.form.dept) is still 'ztest-GhostDept'. Measured: s.form.dept === 'ztest-GhostDept' but the <select>'s input_value() === 'CNC'. If the user then edits some other field (I changed Shift to Night) and saves, the department stays 'ztest-GhostDept' — i.e. the saved record disagrees with what the form showed the user. The form gives no indication the department is unrecognised.
- **Expected:** The select should either surface the employee's actual department (e.g. as a disabled/extra option) or clearly flag it as no longer configured. It must never display a department the employee is not in.
- **Evidence:** Console output from the run: "form.dept state: 'ztest-GhostDept'  <select> shows: 'CNC'" then after changing only Shift and clicking Save: AFTER SAVE (server): {"name": "ztest-employees-alpha", "dept": "ztest-GhostDept", "shift": "N"}.

### [MINOR] A long document label collapses the filename button to 0 px — the filename is invisible and the view/preview control cannot be clicked
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010 and open /employees/. Open any employee's detail drawer.
2. In the Documents section, type a 60-character label with NO spaces into the 'Label (e.g. Aadhaar)' box, e.g. XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX (the server caps labels at 60 chars, so a longer paste lands here too).
3. Click '+ Add files' and attach any PDF with a normal-length name (I used 'ztest-employees-two-a.pdf', 25 chars).
4. Look at the resulting document row, and try to click the filename to preview the document.
- **Observed:** The label pill renders 456 px wide inside a 412 px row, so the filename button — which has `truncate` and therefore shrinks — is squeezed to width 0 px. The filename text is completely invisible and the button is unclickable (Playwright: 'element is not visible', click times out). The ⬇ download and ✕ delete buttons stay at 11 px / 9 px and remain usable, but there is no way to see which file the row refers to or to open it. A 60-char label that contains spaces wraps and keeps the button at ~66 px, so the trigger is specifically a long unbroken label.
- **Expected:** The label should wrap or truncate so the filename stays visible and clickable; the row must never render a zero-width control.
- **Evidence:** Measured in the live DOM for employee 71's document rows: labelLen=60 labW=456 fileBtnW=0 file=ztest-employees-two-a.pdf; labelLen=60 labW=456 fileBtnW=0 file=ztest-employees-two-b.png; (control rows) labelLen=9 labW=67 fileBtnW=159. Playwright click on the filename: 'Locator.click: Timeout ... element is not visible'. Screenshot: employees-doc-filename-collapse.jpg.

### [MINOR] A joining date far in the future is accepted without any validation
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010 and open /employees/.
2. Click '+ Add employee'.
3. Full name: 'ztest-employees-future'. Base salary: 1000. Date joined: 31/12/2099.
4. Click Save, then reload the page and look at the new row's 'Joined' column.
- **Observed:** The record saves with a success toast and stores date_joined = '2099-12-31'; the roster renders '31 Dec 2099'. There is no warning and nothing downstream flags it. (This was my record id 72.)
- **Expected:** A joining date more than a short window into the future should be rejected with a validation message, the same way a blank name and a zero salary already are.
- **Evidence:** Row after reload: {"id": 72, "name": "ztest-employees-future", "dept": "CNC", "base_salary": 1000, "leave_balance": 4, "date_joined": "2099-12-31", "active": 1}; s.formError was '' and the toast read 'ztest-employees-future saved'.

### [MINOR] Fractional leave adjustment below 1 day does nothing at all, with no feedback
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010, open /employees/, open a non-overtime employee (one that shows the 'Leave bank' card).
2. Note the current balance.
3. Clear the small number box between the − and + buttons and type 0.5.
4. Click '+'. Then try '−'.
- **Observed:** Nothing happens: no request is sent, the balance is unchanged, and no toast or inline message appears — the button looks dead. (0.5 is truncated to 0 and the handler returns early.) Entering 2.9 silently applies 2 days; the toast then says '+2' but the box still reads 2.9.
- **Expected:** Either reject the fractional value with a visible message ('whole days only') or round it, rather than making the +/- buttons appear broken.
- **Evidence:** Run log: 'leaveDelta=0.5 sign=1 -> bal=10 toast={show: True, msg: "Leave bank +3 → 10 day(s)"}' — i.e. the toast still shows the PREVIOUS adjustment, no new request fired. Next line: 'leaveDelta=2.9 sign=1 -> bal=12 toast=... Leave bank +2 → 12 day(s)'.

### [COSMETIC] Roster search does not trim whitespace, so a pasted name with a trailing space matches nothing
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010 and open /employees/.
2. Type three spaces into the 'Name / department / number' search box (or paste a name with a trailing space, e.g. 'Sunil Singh ').
- **Observed:** The table shows 'No employees match.' — 0 of 79 rows — because the query is only lower-cased, never trimmed.
- **Expected:** Leading/trailing whitespace in the search box should be ignored; a whitespace-only query should behave like an empty query.
- **Evidence:** Console output: "typed spaces -> filtered: 0 (of 79 )" against 'typed cnc -> filtered 21' and 'typed 70 -> filtered ids: [70]'.

## Customers

### [MINOR] New-customer code preview shows a different code than the one actually assigned for 'M/s ...', 'Messrs ...' and 'Ms ...' names
- **Steps:** 1. Sign in as admin, go to http://127.0.0.1:8010/customers/ and click '+ Add customer'. 2. Type the name 'M/s ztest Preview Steel'. The hint under the Customer code field reads 'will become MSnn'. 3. Click Save. The record that opens is headed 'ZP02 M/s ztest Preview Steel'. Same in the other direction: type 'Ms ztest Sharma Tools' - the hint reads 'will become ZSnn' - Save gives code MZ01. Third variant: 'Messrs ztest Other Steel' previews 'MZnn' and is saved as ZO01.
- **Observed:** The live preview and the assigned code disagree whenever the name starts with M/s, M/S, Messrs or Ms. Observed pairs: preview 'MSnn' vs assigned ZP02; preview 'MZnn' vs assigned ZO01; preview 'ZSnn' vs assigned MZ01. The backend strips only 'm/s' and 'messrs' before tokenising, while the browser preview instead treats the token 'ms' as a noise word - so the two disagree in both directions.
- **Expected:** The 'will become ...' preview should show the same abbreviation the server assigns, i.e. ZPnn for 'M/s ztest Preview Steel'. The code the office is told to expect is not the code the customer ends up with.
- **Evidence:** Preview text captured from the form: 'M/s ztest Preview Steel' -> MSnn (server: ZP02); 'Messrs ztest Preview Works' -> MZnn (server for the same shape: ZO01); 'Ms ztest Sharma Tools' -> ZSnn (server: MZ01). Screenshot: customers-code-preview-mismatch.jpg

### [MINOR] Uncaught TypeErrors thrown every time the customer detail is closed, and the number thrown grows with each open/close cycle
- **Steps:** 1. Open http://127.0.0.1:8010/customers/ with the browser devtools console open. 2. Click a customer row (e.g. BH01) to open the detail modal, then close it with the X button. Nothing is logged. 3. Click the same row again and close it again with X. 13 uncaught exceptions are logged. 4. Repeat twice more: the close throws 26, then 39 exceptions.
- **Observed:** From the second close onward, closing the detail modal throws a burst of uncaught 'TypeError: Cannot read properties of null (reading "stats")', '... (reading "series")', '... (reading "orders")' and '... (reading "documents")'. The count grows by exactly 13 per open/close cycle (measured 0, 13, 26, 39 over four cycles), i.e. dead Business-tab bindings from previous opens are never released and re-evaluate against biz === null on every close. The UI keeps working, but the leak is unbounded within a page session.
- **Expected:** Closing the detail should tear the modal down without throwing, and the error count should not grow with usage.
- **Evidence:** Playwright pageerror capture, cycle 1: 0 errors; cycle 2: 13; cycle 3: 26; cycle 4: 39. Unique messages: ["Cannot read properties of null (reading 'documents')", "Cannot read properties of null (reading 'orders')", "Cannot read properties of null (reading 'series')", "Cannot read properties of null (reading 'stats')"]

### [MINOR] After creating a customer the record opens on an empty Business tab and the Business button is dead until you close and re-open
- **Steps:** 1. Open http://127.0.0.1:8010/customers/, open any customer (e.g. BH01) and click the Business tab. 2. Close the modal with X. 3. Click '+ Add customer', type 'ztest Tabstate Co' and click Save.
- **Observed:** The newly created customer's modal opens with the Business tab highlighted and a completely empty body - no stat tiles, no chart, not even the 'No orders yet.' message (the modal is ~2 cm tall, just the header). Clicking the Business button again does nothing: the panel stays blank because the business data is only fetched when a customer is opened from the list, and saving a new customer never fetches it (state stays biz: null). The only way to see the Business tab for that customer is to close the modal and re-open the row from the list.
- **Expected:** A freshly saved customer should open on the Profile tab (or should load its business data), and the Business tab should at minimum show the zeroed tiles and 'No orders yet.'
- **Evidence:** State after save: tab='business', biz=null, detail={code:'ZT03', name:'ztest Tabstate Co'}; the business panel is visible with inner text ''. Screenshot: customers-new-customer-blank-business-tab.jpg

### [MINOR] Business tab 'Print' button opens a raw JSON 403 page for a user who has the customers grant but not quotations
- **Steps:** 1. As admin create an operator account with grants ['customers'] only (POST /api/users {username:'ztest-cust-only', password:'ztest123', role:'operator', grants:['customers']}). 2. Sign in as that user - the launcher shows only the Customers tile - and go to /customers/. 3. Open BH01, click the Business tab (the Quotations & invoices list renders fine, 11 documents with totals). 4. Click 'Print' on any document row.
- **Observed:** A new tab opens at http://127.0.0.1:8010/api/quotations/17/print showing the raw text {"detail":"Your account doesn't have access to this module"} (HTTP 403). Every Print button in the Customers module is broken for this account, with no in-app message. The same click as admin returns the proper A4 print page.
- **Expected:** Either the Print buttons should not be offered to an account that cannot reach them, or the print view should be served under the customers grant / fail with a readable in-app error instead of a raw JSON body in a new tab.
- **Evidence:** GET /api/quotations/1/print as ztest-cust-only -> 403 {"detail":"Your account doesn't have access to this module"}; popup URL /api/quotations/17/print, body text {"detail":"Your account doesn't have access to this module"}. Screenshot: customers-limited-print.jpg. (The test account was deleted afterwards.)

### [MINOR] Leaving the 'Their Rs/hour' field empty silently saves a 0/hour customer rate and reports success
- **Steps:** 1. Open /customers/, open ZA01 (ztest Alpha Castings) and click the Rates tab. 2. Choose 'Drilling' in the Operation dropdown - the Their Rs/hour box prefills with the standard 300. 3. Clear that box (or type letters into it: the number input discards 'abc' and leaves it empty). 4. Click Save. 5. Reload the page and re-open the customer's Rates tab.
- **Observed:** A green toast 'Rate saved for this customer' appears and the table gains a row 'Drilling | Standard Rs 300 | Their Rs 0 | Add'l - | Effective Rs 0', which is still there after a full page reload. The empty field is coerced to 0 client-side, so the customer silently ends up with a negotiated rate of Rs 0/hour for that operation - the panel's own text says these rates prefill the costing workspace instead of the standard rates. Negative values and values above 1e9 ARE rejected by the server, so the field is validated for everything except 'nothing'.
- **Expected:** Saving with an empty rate should be refused (like the empty operation is, with 'Pick an operation'), or at least require an explicit 0.
- **Evidence:** State at save time: rateRow={operation:'Drilling', rate_per_hour:'', extra_rate:'', note:'ztest blank rate'}; toast 'Rate saved for this customer'; stored row ['Drilling', 0, 0, 'ztest blank rate']; after reload still ['Drilling', 0, 'ztest blank rate']. UI row text: 'Drilling  Rs 300  Rs 0  -  Rs 0  ztest blank rate'.

### [MINOR] Duplicate-name check is case-sensitive, so the same customer can be entered twice with different capitalisation
- **Steps:** 1. Open /customers/, click '+ Add customer', enter 'ztest Alpha Castings' and Save (created as ZA01). 2. Click '+ Add customer' again, enter the same name in capitals: 'ZTEST ALPHA CASTINGS'. 3. Click Save. 4. Search the list for 'ztest Alpha'.
- **Observed:** The capitalised name is accepted and stored as a second customer, code ZA04. The list now shows both 'ztest Alpha Castings' (ZA01) and 'ZTEST ALPHA CASTINGS' (ZA04) as separate customers with separate codes, orders and rates. Re-entering the name with identical capitalisation is correctly rejected with "'ztest Alpha Castings' already exists", and leading/trailing spaces are trimmed before the check - only case slips through.
- **Expected:** The duplicate check should be case-insensitive (the list itself sorts COLLATE NOCASE), so the second attempt should be refused with the same 'already exists' message.
- **Evidence:** POST /api/customers {name:'ZTEST ALPHA CASTINGS'} -> 200 with code ZA04; list rows include ['ZA01','ztest Alpha Castings'] and ['ZA04','ZTEST ALPHA CASTINGS'].

### [COSMETIC] Empty search results show the first-run message 'No customers yet - add the first with "+ Add customer"'
- **Steps:** 1. Open /customers/ with customers present. 2. Type 'zzzz-no-such-customer' into the Search box and wait for the list to refresh.
- **Observed:** The table shows 'No customers yet - add the first with "+ Add customer".' even though the database is full of customers and the list is empty only because of the search filter.
- **Expected:** Something like 'No customers match this search.'
- **Evidence:** Cell text captured: 'No customers yet - add the first with “+ Add customer”.' with 0 rows and search='zzzz-no-such-customer'. Screenshot: customers-empty-search.jpg

### [COSMETIC] Removing a customer rate gives no confirmation feedback
- **Steps:** 1. Open /customers/, open ZA01, Rates tab. 2. Click the X on a rate row and accept the confirm dialog.
- **Observed:** The row disappears but no toast is shown, unlike every other write in the module ('Rate saved for this customer', 'Customer deactivated', etc.). State after the delete: toast={show:false, msg:''}.
- **Expected:** A short confirmation toast, consistent with the other actions.
- **Evidence:** toast state after a successful rate removal: {'show': False, 'msg': '', 'kind': 'ok'}; the rate list went from ['CNC Milling','Grinding'] to ['CNC Milling'].

## Parts & Pricing

### [MINOR] Closing the costing workspace throws uncaught TypeErrors, and one more batch of 5 is added on every subsequent open/close cycle
- **Steps:** 1. Sign in as admin at http://127.0.0.1:8010 and open /parts/.
2. Search 'DRG-4711' and click the first row to open the drawing modal (no records need to be created).
3. Click 'Open costing workspace →', then click '← Back'. Console is clean on this first cycle.
4. Click 'Open costing workspace →' a second time, then '← Back'.
5. Repeat a third time. Watch the browser console (or a Playwright pageerror listener).
The same happens instead of Back if you click 'Save costing' (a successful save also nulls the costing) or if you switch revision with the A/B pills inside the workspace.
- **Observed:** Cycle 1: 0 errors. Cycle 2: 5 uncaught TypeErrors. Cycle 3: 10. Cycle 6: 25 (75 uncaught exceptions after six open/close cycles). The messages are "Cannot read properties of null (reading 'ops')", "...(reading 'material_cost')", "...(reading 'margin_pct')" (x2) and "...(reading 'notes')" — i.e. the previous workspace instance's bindings are still live and re-evaluate against costing === null after closeWorkspace()/saveCosting() sets it to null. The visible UI keeps working (row arithmetic was still correct at cycle 6), but the exception count grows linearly with use.
- **Expected:** Closing or saving out of the costing workspace should tear down its bindings cleanly and raise no uncaught exceptions, no matter how many times the workspace is opened in one page session.
- **Evidence:** pageerror log, cycles on drawing DRG-4711: cycle 1 = 0, cycle 2 = 5, cycle 3 = 10; six cycles on ztest-parts-1 gave 0/5/10/15/20/25 = 75 total. Sample: "Cannot read properties of null (reading 'margin_pct')" at http://127.0.0.1:8010/parts/

### [MINOR] Double-clicking 'Save costing' silently writes two identical costings
- **Steps:** 1. Open /parts/, open a drawing, click 'Open costing workspace →'.
2. Pick operation 'Drilling', set Minutes = 20 (leave everything else default).
3. Double-click the 'Save costing' button (two clicks in quick succession — the button is never disabled and there is no in-flight guard).
4. Re-open the drawing (or reload the page) and look at 'Saved costings for this revision'.
- **Observed:** Two separate costing records are created with the same values and the same created_at second — e.g. ids 40 and 41, both ₹100 /piece, both '2026-08-14T15:19:23', both with a single Drilling row. Earlier run produced ids 25 and 26 (₹52.5 each). Both rows persist after a page reload; the user only ever saw one 'Costing saved' toast.
- **Expected:** One click-through should produce exactly one costing; the second submission should be ignored (button disabled / in-flight guard) rather than silently duplicating priced build-ups on the drawing.
- **Evidence:** costings before dblclick: 1, after: 3 -> [[41, 100, "2026-08-14T15:19:23", ["Drilling"]], [40, 100, "2026-08-14T15:19:23", ["Drilling"]], [39, 1.97, ...]]

### [MINOR] Row total shown in the workspace can be one paisa higher than the figure actually saved (UI rounds half-up, server rounds half-to-even)
- **Steps:** 1. Open /parts/, open drawing 'ztest-parts-3' (id 9, no customer assigned — I created it for this repro; any drawing with no customer works).
2. Click 'Open costing workspace →'.
3. Row 1: Operation = Turning, Minutes = 1, ₹/hour = 7.5, Add'l ₹/hour = 0, Weightage = 1. Leave material and margin blank.
4. Read 'Row ₹' and the totals block, then click 'Save costing'.
5. Look at the saved costing card on the drawing (and reload the page to confirm what persisted).
Other values that hit it: 0.5 min @ ₹15, 2 min @ ₹3.75, 1 min @ ₹0 + ₹7.5/hr — any row whose cost lands exactly on a half-paisa.
- **Observed:** The workspace shows Row ₹ = ₹0.13 and 'Operations (weighted) ₹0.13 / + material ₹0.13 / + overall margin 0% ₹0.13'. The saved costing card reads '₹0.12 /piece — Turning 1min ₹0.12', and after a full reload the stored record is cost 0.12, total 0.12. The user approves ₹0.13 and the system keeps ₹0.12.
- **Expected:** The figure the user sees in the workspace and the figure written to the costing (and later converted into the rate history) must be identical — the frontend Math.round(x*100)/100 and the backend round(x, 2) must agree.
- **Evidence:** UI row: ₹0.13 | stored: {"id": 42, "cost": 0.12, "total": 0.12}; matrix run flagged 4/12 cases ROW-UI!=DB, all half-paisa values (js=0.13 vs py=0.12). Same on a customer-rate row: UI ₹1.98 vs stored ₹1.97 (costing id 39).

### [MINOR] Weightage 0 is accepted by the row editor (row shows ₹0) but makes the entire costing unsaveable
- **Steps:** 1. Open /parts/, open a drawing, click 'Open costing workspace →'.
2. Build several valid rows (e.g. Turning 30 min, Milling 45 min +₹50/hr, Drilling 60 min).
3. Add one more row: Operation = Deburring, Minutes = 12, ₹/hour = 200, Weightage = 0 (the input has min="0", so 0 is a legal entry and the row displays ₹0).
4. Fill material/margin and click 'Save costing'.
- **Observed:** The workspace happily shows the zero row (Row ₹ = ₹0) and includes it in the totals, but the POST returns 400 and the toast says 'Deburring: weightage must be a normal, non-zero positive number'. Nothing is saved at all — the whole build-up is rejected because of one row, and the offending field is only identifiable from the operation name in the toast.
- **Expected:** Either the weightage input should refuse 0 at entry (min > 0 / inline field error on the row), or the server should accept a 0-weightage row as a ₹0 line. The UI should not present a state it knows the server will reject wholesale.
- **Evidence:** HTTP 400 POST /api/parts/drawings/5/costings -> toast 'Deburring: weightage must be a normal, non-zero positive number'; the same row displayed '₹0' in the workspace before saving.

### [COSMETIC] Search box passes SQL LIKE wildcards straight through
- **Steps:** 1. Open /parts/.
2. Type '%' into the Search box and wait for the debounce.
3. Then type 'ztest_parts-1'.
- **Observed:** '%' returns every drawing (6 rows) instead of none, and 'ztest_parts-1' matches 'ztest-parts-1' because '_' is treated as a single-character wildcard.
- **Expected:** '%' and '_' typed by a user should be searched for literally (escaped) rather than acting as wildcards.
- **Evidence:** search '%' -> 6 rows; search 'ztest_parts-1' -> 2 rows (the ztest-parts-1 A and B revisions)

## Refuted (reported but could not be reproduced)

- **Salary & Attendance** — An employee with no attendance for the period is published at Rs0 with no confirmation  
  _why dropped:_ I reproduced the mechanics but not the defect, and the word 'silently' is a misreading of the UI. Prepare 2026-07 with my employee 86 (no summary for that month): the row comes back {has_attendance:false, present_days:null, attendance_percentage:null}; after Calculate it is {attendance_percentage:0, base_att:0, total:0, error:null} and s.payErrors() does not include it. But the screen does flag it: the Days input renders class 'border-rose-300' with computed borderColor rgb(253,164,175) against rgb(229,231,235) on every normal row, the Att% cell shows an em dash, and the Total column plainly shows 0 before anyone can press Publish. Rs0 for zero attendance is also arithmetically correct, not a miscomputation - an employee who genuinely worked no days must be publishable at 0, so blocking the row would be the bug. I did not press Publish; the only published zero-total row in the DB (GET /api/pay?period=2026-06 -> employee 79 'ztest-employees-79', base 15000, att 0, total 0, and 0 negative totals across 2026-06 and 2026-07) was written by the reporter's own run, so 'is published with no confirmation' rests on their action, and the negative-total variant they describe is explicitly hypothetical ('had that employee been PF/ESI-eligible'). At most this is a request for a confirmation dialog.
- **Salary & Attendance** — The X-Requested-With guard the client documents is not enforced by the server  
  _why dropped:_ The observation is accurate but it is not a defect - there is no leak and nothing behaves wrongly. Reproduced: with an admin session cookie and no X-Requested-With, POST /api/advances returns 400 {"detail":"Advance amount must be positive"}; grep confirms the string appears nowhere in backend/. However (a) the comment in payroll.js says state-changing routes *can* require the header, i.e. it describes why the header is attachable as a guard, not that any route enforces it; (b) the session cookie is set HttpOnly; SameSite=lax (verified on the Set-Cookie of /api/login), so a hostile page's cross-site POST carries no cookie at all; (c) the only cross-origin request shapes that avoid a preflight are rejected anyway - I sent the same body as application/x-www-form-urlencoded and as text/plain and both returned 422 'Input should be a valid dictionary or object'; (d) with no cookie the route returns 401 'Not logged in'. So the documented protection is redundant, not missing, and nothing a user or attacker can do is affected.
- **Settings** — A rate the server rejected keeps being displayed in the rates table as if it were the stored value  
  _why dropped:_ I reproduced the mechanics precisely but this is not a defect. Set the row to 300 (saved). Typed -5, Save -> PUT /api/settings/operations/28 returned 400, red toast 'Rate must be a normal, non-negative number', and the input keeps showing '-5'. Same for 1000000001 and 99999999999999999999. GET /api/settings during that window returns rate_per_hour 300 every time, so no data is wrong. What the reporter calls 'the rates table showing a wrong figure' is an editable <input type=number> still holding the text the user just typed after a rejected save - the normal, and usually desirable, behaviour, since the user needs to see and correct their bad entry. An error WAS shown at the moment of failure. Nothing downstream reads that box: new costings pull rates from /api/parts/refs (server side). And the display is not 'indefinite' as claimed: besides reload, it self-corrects on the very next successful settings action, because that response replaces this.data. I verified this - with '-5' sitting in row A I clicked Save on a DIFFERENT row; row A immediately reverted to '300'. The only genuine gap is the absence of a persistent unsaved-row marker after the toast fades, which is cosmetic.
- **Settings** — Case-variant duplicates are accepted for units, operations and departments  
  _why dropped:_ The storage behaviour reproduces (units 49->50->51 with both 'ztest-settings-unit' and 'ZTEST-SETTINGS-UNIT'; ops 22->23->24 with id 30 'ztest-settings-op'@500 and id 31 'ZTEST-SETTINGS-OP'@77; departments both variants appended), and exact/space-padded duplicates are correctly ignored. But the damaging part of the claim is false. The reporter says the costing picker 'offers two entries that look identical to the user' so 'which rate a quote uses depends on invisible capitalisation'. Capitalisation is not invisible: I opened Parts -> DRG-4711 -> costing workspace and the dropdown lists them with their actual casing. Selection is an exact string match (opPicked does lists.operations.find(o => o.name === op.operation)), so picking each one prefills its OWN rate and the number is displayed right there in the row's editable Rate/hour cell - I picked 'ztest-settings-op' and got rate_per_hour 555, picked 'ZTEST-SETTINGS-OP' and got 77, both visible on screen before anything is priced. No wrong figure is computed, nothing is overwritten, no save fails. Case-sensitive uniqueness with case-insensitive ordering is a data-hygiene preference, not a user-visible wrong behaviour. Downgraded from minor to cosmetic.

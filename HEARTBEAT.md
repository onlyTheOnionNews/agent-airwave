# OpenClaw Event Loop: Silicon Swarm 4G

## Global Configuration
- **Poll Interval:** 5 seconds
- **Watch Directory:** `.` (Current workspace directory)
- **Max Concurrent Agents:** 1 (Strict sequential pipeline)

## Event Triggers & Routing

### 1. Spec Generation (Triggering the Librarian)
- **Watch:** `tasks/*.txt` (e.g., `tasks/lte_pss_gen.txt`)
- **Condition:** File is `NEW`.
- **Action:** Wake **3GPP Librarian**.
- **Input:** Pass the contents of the `.txt` file.
- **Expected Output:** Librarian writes `specs/[module_name]_spec.md`.
- **Post-Action:** Move `.txt` to `tasks/completed/`.

### 2. RTL Coding (Triggering the RTL Architect)
- **Watch:** `specs/*.md` OR `feedback/*.json`
- **Condition:** File is `NEW` or `MODIFIED`.
- **Action:** Wake **Lead RTL Architect**.
- **Input:** - If triggered by `specs/*.md`, pass the spec to write new RTL.
  - If triggered by `feedback/*.json` (Judge or PD Lead error), pass the JSON and the current `rtl/[module_name].sv` for bug fixing.
- **Expected Output:** Architect writes or updates `rtl/[module_name].sv`.
- **Post-Action:** Archive the triggering `feedback/*.json` to `feedback/archive/` to prevent infinite loops.

### 3. Verification (Triggering the Judge)
- **Watch:** `rtl/*.sv`
- **Condition:** File is `MODIFIED` (Architect just saved new code).
- **Action:** Wake **Verification Judge**.
- **Input:** Execute EDA toolchain against `rtl/[module_name].sv`.
- **Expected Output:** - If FAIL: Judge writes `feedback/[module_name]_judge.json`.
  - If PASS: Judge writes an empty flag file `status/[module_name]_VERIFIED.flag`.

### 4. Physical Implementation (Triggering the PD Lead)
- **Watch:** `status/*_VERIFIED.flag`
- **Condition:** File is `NEW`.
- **Action:** Wake **Physical Design Lead**.
- **Input:** Execute OpenLane flow on the verified `rtl/[module_name].sv`.
- **Expected Output:**
  - If FAIL: PD Lead writes `feedback/[module_name]_phys.json`.
  - If PASS: PD Lead writes `status/[module_name]_GDSII.flag` and outputs metrics.
- **Post-Action:** Delete the `_VERIFIED.flag` so it doesn't re-trigger.

### 5. Swarm Governance (Triggering the CFO/Orchestrator)
- **Watch:** `feedback/*.json`
- **Condition:** JSON `retry_count` key is >= 3.
- **Action:** Wake **Cost-Control Orchestrator**.
- **Input:** Pass the failing JSON payload.
- **Expected Output:** Orchestrator writes `status/HALTED.flag` and executes the `message_user` tool to request Human-in-the-Loop intervention.
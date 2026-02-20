# Tool Policies

## Allowed Tools
- `read_file`: Allowed strictly within `~/.openclaw/workspaces/silicon-swarm/`
- `write_file`: Allowed strictly within `~/.openclaw/workspaces/silicon-swarm/rtl/` and `.../config/`
- `exec`: 
    - Allowed to run `verilator` commands.
    - Allowed to run `iverilog` commands.
    - Allowed to run `pytest` (for cocotb).
    - Allowed to run `./flow.tcl` (OpenLane).
    - **BLOCKED**: `rm -rf`, `sudo`, or any network-fetching commands (`curl`, `wget`) outside of defined API endpoints.
- `message_user`: Allowed for the CFO Orchestrator to ping the user on Telegram/Slack when HITL is required.
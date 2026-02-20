# Silicon Swarm 4G

An autonomous agent swarm for designing 4G LTE baseband modem components using OpenClaw event-driven architecture.

## Quick Start

### Option 1: Run Locally (Recommended for Development)

1. **Ensure Python 3.8+ is installed**
2. **Run the swarm:**
   ```bash
   ./run_swarm.sh
   ```

   This script will:
   - Check dependencies and install if needed
   - Initialize the workspace
   - Run the autonomous agent pipeline

### Option 2: OpenClaw Integration (Production)

1. **Clone or copy this repository to the OpenClaw workspace:**
   ```bash
   cp -r silicon-swarm-4g ~/.openclaw/workspaces/
   # Or clone directly:
   git clone <repo-url> ~/.openclaw/workspaces/silicon-swarm-4g
   ```

2. **Initialize the workspace:**
   ```bash
   cd ~/.openclaw/workspaces/silicon-swarm-4g
   ./init_workspace.sh
   ```

3. **Start the OpenClaw daemon** (assuming OpenClaw is installed and configured).

The swarm will automatically begin processing the initial task (`tasks/lte_pss_gen.txt`) and proceed through the agent pipeline.

## Directory Structure

- `personas/` - Agent definitions and prompts
- `tasks/` - Input task files (`.txt`)
- `specs/` - Generated specifications (`.md`)
- `rtl/` - SystemVerilog RTL code (`.sv`)
- `feedback/` - Error feedback and corrections (`.json`)
- `status/` - Status flags (`.flag`)
- `logs/` - Tool execution logs
- `golden_vectors/` - Reference test vectors
- `models/` - AI models and configurations
- `openlane_config/` - Physical design configurations
- `testbenches/` - Verification testbenches
- `scripts/` - Utility scripts

## Agent Pipeline

1. **3GPP Librarian** - Generates specifications from task descriptions
2. **Lead RTL Architect** - Writes SystemVerilog code from specs
3. **Verification Judge** - Runs EDA tools and validates RTL
4. **Physical Design Lead** - Performs ASIC synthesis and layout
5. **Cost-Control Orchestrator** - Manages retries and budget

See `HEARTBEAT.md` for detailed event triggers and routing.

## Requirements

- OpenClaw daemon running
- EDA tools (Verilator, Icarus Verilog, Cocotb, OpenLane)
- Python 3.8+ with required packages
- Access to 3GPP specifications

## Adding New Tasks

Create a new `.txt` file in the `tasks/` directory with your module requirements. The swarm will automatically detect and process it.
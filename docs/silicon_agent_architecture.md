# Silicon-Agent Workflow Architecture: Autonomous 4G LTE Baseband Modem Design

**Version 1.0 — February 2026**
**Deliverable Type: Dual Execution Plans (Cloud + Local)**

---

## Executive Summary

This document presents two complete agentic workflow architectures for the autonomous design of an open-source 4G LTE baseband modem — from 3GPP specification ingestion through GDSII physical layout. **Plan A** leverages unlimited cloud compute and frontier API models. **Plan B** operates entirely within a single RTX 4090 (24 GB VRAM) and 64 GB system RAM, using open-weights models. Both plans share an identical agent topology, verification loop, and execution roadmap; they differ only in model selection, orchestration hosting, and memory constraints.

The target design encompasses the LTE PHY-layer baseband: OFDM modulator/demodulator, turbo encoder/decoder, resource-element mapper, channel estimator, and a minimal MAC-layer DL-SCH transport channel processor — sufficient for a Cat-1 UE downlink data path as defined in 3GPP TS 36.211, 36.212, 36.213, and 36.300.

---

## Table of Contents

1. [Part I — The "Unlimited" Cloud Stack (Plan A)](#part-i)
2. [Part II — The "Sovereign" Local Stack (Plan B)](#part-ii)
3. [Part III — Step-by-Step Execution Roadmap](#part-iii)
4. [Appendix A — Agent System Prompts](#appendix-a)
5. [Appendix B — Cost Model Detail](#appendix-b)
6. [Appendix C — 3GPP Spec Ingestion Pipeline](#appendix-c)

---

<a name="part-i"></a>
## Part I: The "Unlimited" Cloud Stack (Plan A)

### 1.1 Orchestration Framework

**Framework: LangGraph (v0.2+) hosted on LangSmith Cloud**

LangGraph is selected over AutoGen and CrewAI for one decisive reason: it provides **explicit, cyclic state machines** with checkpointed persistence. AutoGen's conversation-based agent loop is too implicit for a verification-driven hardware design flow where an RTL block may cycle through compile → simulate → fix 15+ times before convergence. CrewAI's sequential/hierarchical process model cannot express the branching re-entry points our self-healing loop requires.

**State Management:**

The orchestrator maintains a single `DesignState` object persisted to a PostgreSQL-backed LangGraph checkpoint store. This state object contains:

```python
@dataclass
class DesignState:
    project_phase: Literal["spec", "rtl", "verify", "pnr"]
    block_registry: dict[str, BlockStatus]  # e.g. {"ofdm_mod": PASSED, "turbo_dec": FAILING}
    spec_context: list[str]                 # Active 3GPP section references
    rtl_artifacts: dict[str, str]           # block_name -> file path in artifact store
    testbench_artifacts: dict[str, str]
    error_log: list[ErrorRecord]            # Structured stderr captures
    iteration_counts: dict[str, int]        # block_name -> retry count
    synthesis_reports: dict[str, dict]       # block_name -> area/timing/power
    token_budget_remaining: float
    human_escalation_queue: list[str]
```

Every agent invocation reads from and writes to this state. LangGraph's built-in `MemorySaver` with PostgreSQL backend guarantees that if any agent call fails (API timeout, OOM), the workflow resumes from the last committed checkpoint — not from scratch.

**Graph Topology:**

```
                    ┌──────────────┐
                    │  Orchestrator │
                    │  (Router)     │
                    └──────┬───────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
    ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
    │ 3GPP        │ │ RTL         │ │ Verification│
    │ Librarian   │ │ Engineer    │ │ Engineer    │
    └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
           │               │               │
           │               │        ┌──────▼──────┐
           │               │        │   Judge      │
           │               │        │  (Gatekeeper)│
           │               │        └──────┬──────┘
           │               │               │
           │               │        ┌──────▼──────┐
           │               │        │  PnR Lead   │
           │               │        │ (OpenLane)  │
           │               │        └─────────────┘
           │               │
           └───────────────┘
              feedback loops
```

### 1.2 Model Selection (Cloud)

| Agent Role | Primary Model | Rationale |
|---|---|---|
| **Orchestrator / Router** | `claude-sonnet-4-20250514` | Fast, cheap routing decisions. Does not generate RTL — only decides which agent to invoke next and marshals state. Sonnet's structured output mode guarantees valid JSON state transitions. |
| **3GPP Librarian** | `claude-sonnet-4-20250514` + RAG | Retrieval-heavy role. Sonnet is ideal for synthesizing retrieved chunks into precise, referenced specification excerpts. Does not need frontier reasoning. |
| **RTL Engineer** | `claude-opus-4-20250514` (primary), `o3` (fallback for algorithmic blocks) | Opus for SystemVerilog generation — it has the strongest adherence to complex structural constraints. o3 is invoked specifically for algorithmically dense blocks (turbo decoder trellis, FFT butterfly) where extended chain-of-thought reasoning improves correctness. |
| **Verification Engineer** | `claude-opus-4-20250514` | UVM testbench generation requires maintaining coherence across large file structures (class hierarchies, factory registrations, sequence libraries). Opus handles this context length well. |
| **Judge / Gatekeeper** | `claude-sonnet-4-20250514` | Parses stderr/stdout logs, classifies errors, decides pass/fail. Speed matters here because this agent is invoked on every compilation attempt. |
| **Physical Design Lead** | `claude-sonnet-4-20250514` | Generates OpenLane configuration TCL/JSON. This is templated work — Sonnet is sufficient and cost-efficient. |

**Why not GPT-4.5 / GPT-5 everywhere?** OpenAI's models as of early 2026 are competitive on general code but consistently underperform Anthropic's Claude family on SystemVerilog specifically. This is empirically measurable: Claude Opus produces syntactically valid `always_ff` blocks with correct non-blocking assignments (`<=`) at a higher rate than GPT-4.5 in our internal benchmarks. The o3 model is reserved for pure algorithmic reasoning (e.g., deriving the trellis state transitions for a max-log-MAP turbo decoder) where its extended thinking capabilities provide a measurable advantage.

### 1.3 Specialist Agent Definitions (Cloud)

#### 1.3.1 The Orchestrator

**System Prompt Core:**
```
You are the Silicon-Agent Orchestrator. You manage the design of a 4G LTE
baseband modem. You NEVER write RTL or testbenches yourself. Your sole job
is to: (1) read the current DesignState, (2) decide which specialist agent
to invoke next, (3) formulate a precise task description for that agent
including all necessary spec references, and (4) update the DesignState
with results. You follow this priority order: blocks in FAILING state get
re-routed to the RTL Engineer with error context before any new blocks
are started. You enforce a maximum of 8 retry iterations per block before
escalating to the human review queue.
```

**Tools:**
- `state_read()` — Reads current `DesignState` from LangGraph checkpoint
- `state_write(patch)` — Atomic update to `DesignState`
- `dispatch_agent(agent_id, task_payload)` — Invokes a specialist agent
- `escalate_to_human(block_name, reason)` — Writes to Slack/Discord webhook

#### 1.3.2 The 3GPP Librarian (RAG Searcher)

**System Prompt Core:**
```
You are the 3GPP Specification Librarian. You have access to a vector
database containing the complete text of 3GPP TS 36.211 (Physical Channels
and Modulation), TS 36.212 (Multiplexing and Channel Coding), TS 36.213
(Physical Layer Procedures), and TS 36.300 (E-UTRAN Overall Description).
When asked a question, you MUST: (1) retrieve the most relevant sections
using semantic search, (2) quote the exact specification text with section
numbers, (3) resolve any cross-references to other sections, (4) present
the information in a structured format suitable for an RTL engineer to
implement. You NEVER hallucinate specification content. If you cannot find
the answer, say so explicitly.
```

**Tools:**
- `vector_search(query, top_k=10, spec_filter=None)` — Searches the 3GPP vector store. Returns chunks with section IDs, page numbers, and relevance scores.
- `fetch_section(spec_id, section_number)` — Retrieves the full text of a specific section (e.g., `fetch_section("36.211", "6.3.1")`)
- `cross_reference_resolver(section_id)` — Follows "see Section X.Y.Z" references and returns the target content
- `table_extractor(spec_id, table_number)` — Returns structured data from specification tables (e.g., modulation order mappings, resource block definitions)

**3GPP Ingestion Pipeline (Cloud):**

The 20,000+ pages of 3GPP specs cannot fit in any context window. The ingestion pipeline works as follows:

1. **PDF Parsing:** All 3GPP specification PDFs are parsed using `pdfplumber` with custom table extraction rules. Mathematical formulas are extracted as LaTeX using `Nougat` (Meta's academic document OCR model). Each section is split at heading boundaries (detected via font-size heuristics in the PDF metadata).

2. **Chunking Strategy:** Hierarchical chunking — each chunk preserves its full section path (e.g., `TS 36.211 > 6 > 6.3 > 6.3.1 OFDM baseband signal generation`). Chunk size is 512 tokens with 128-token overlap. Tables are stored as separate chunks with their caption as metadata.

3. **Embedding & Storage:** Chunks are embedded using `voyage-3-large` (1024-dim, optimized for technical retrieval). Stored in **Pinecone** (serverless) with metadata filters on `spec_id`, `section_path`, `content_type` (prose / table / equation).

4. **Retrieval Strategy:** Hybrid search — vector similarity (cosine, top-20) + BM25 keyword search (top-20) → reciprocal rank fusion → re-ranked by `cohere-rerank-v3.5` to top-10. This ensures that exact specification terminology (e.g., "Resource Element Mapper") is found even when the semantic embedding doesn't rank it highest.

#### 1.3.3 The RTL Engineer (SystemVerilog Coder)

**System Prompt Core:**
```
You are a senior RTL Engineer specializing in SystemVerilog for ASIC design.
You write synthesizable SystemVerilog-2017 code targeting ASIC synthesis via
Yosys/OpenLane. You follow these STRICT rules:
- Use `always_ff @(posedge clk)` for sequential logic, `always_comb` for
  combinational logic. NEVER use `always @(*)`.
- All signals must be explicitly typed (`logic`, `wire`). No implicit nets.
- Use non-blocking assignments (`<=`) in sequential blocks only.
- Every module must have a synchronous active-low reset (`rst_n`).
- Include a standard AXI-Stream interface (tvalid/tready/tdata/tlast) on
  all data-path module ports for composability.
- Write self-documenting code with comments referencing the 3GPP section
  that each functional block implements.
- Target clock: 100 MHz. Pipeline aggressively to meet timing.
- Output ONLY the complete SystemVerilog file. No explanations unless asked.

You will receive: (1) a natural-language specification derived from 3GPP
documents, (2) interface definitions for upstream/downstream modules, and
(3) if this is a RETRY, the previous code and the exact error messages
from compilation/simulation.
```

**Tools:**
- `write_artifact(filename, content)` — Saves a `.sv` file to the artifact store
- `read_artifact(filename)` — Reads back a previously written file
- `query_librarian(question)` — Asks the 3GPP Librarian agent a question mid-generation (e.g., "What is the exact polynomial for the CRC-24A used in transport block CRC attachment per TS 36.212 Section 5.1.1?")

#### 1.3.4 The Verification Engineer (UVM/Testbench Writer)

**System Prompt Core:**
```
You are a Verification Engineer specializing in UVM-based testbenches for
digital ASIC designs. For each RTL module you receive, you produce:
1. A self-checking SystemVerilog testbench (non-UVM) for quick smoke tests
   that can run on Icarus Verilog (iverilog).
2. A full UVM testbench (for Verilator or commercial simulators) including:
   - uvm_env with scoreboard, coverage collector
   - uvm_sequence_item matching the DUT's AXI-Stream interface
   - uvm_driver, uvm_monitor, uvm_sequencer
   - At least one directed test and one constrained-random test
3. A reference model in Python that generates expected output vectors for
   given input stimulus, based on the 3GPP specification. This is used by
   the scoreboard for output comparison.

For iverilog-compatible testbenches: avoid UVM, use $dumpfile/$dumpvars for
waveform output, use $readmemh/$readmemb for stimulus loading, and assert
via `if/else` with `$error` calls.

You ALWAYS generate stimulus that exercises boundary conditions: minimum
and maximum resource block allocations, all modulation orders (QPSK, 16QAM,
64QAM), and reset-during-active-transfer scenarios.
```

**Tools:**
- `write_artifact(filename, content)` — Saves `.sv` / `.py` testbench files
- `read_artifact(filename)` — Reads RTL source to understand interfaces
- `run_python(script)` — Executes a Python reference model to generate golden vectors (`.hex` files)
- `query_librarian(question)` — For spec clarifications

#### 1.3.5 The Physical Design Lead (OpenLane/GDSII Handler)

**System Prompt Core:**
```
You are a Physical Design Lead using the OpenLane 2 (ORFS-based) open-source
RTL-to-GDSII flow targeting the SkyWater SKY130 PDK. You generate:
1. The OpenLane `config.json` for each macro, specifying:
   - CLOCK_PERIOD (target: 10ns = 100MHz)
   - FP_CORE_UTIL (start at 40%, increase if area permits)
   - PL_TARGET_DENSITY (0.55 default)
   - SYNTH_STRATEGY ("AREA 0" for area-critical, "DELAY 3" for timing-critical)
   - Die area / floorplan for the top-level SoC integration
2. The Makefile or shell script to invoke the OpenLane flow.
3. Interpretation of synthesis and PnR reports — flag timing violations,
   DRC errors, and antenna violations. Suggest remediation (e.g., buffer
   insertion, de-rating utilization, restructuring critical paths).

You target the sky130_fd_sc_hd standard cell library. For memories (e.g.,
turbo decoder interleaver tables), use OpenRAM-generated SRAMs or
synthesized register files depending on size.
```

**Tools:**
- `read_artifact(filename)` — Reads RTL files and synthesis reports
- `write_artifact(filename, content)` — Writes config files, scripts
- `run_shell(command)` — Executes OpenLane flow steps and captures output
- `parse_report(report_path)` — Structured parsing of Yosys/OpenSTA/Magic reports

### 1.4 The "Judge" Mechanism — Self-Healing Verification Loop

This is the critical feedback mechanism that enables autonomous convergence.

**Loop Architecture:**

```
┌─────────────────────────────────────────────────────────────────┐
│                    JUDGE VERIFICATION LOOP                       │
│                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │ RTL      │──▶│ Compile  │──▶│ Simulate │──▶│ Judge    │    │
│  │ Engineer │   │ (iverilog│   │ (run tb) │   │ Evaluates│    │
│  │ writes   │   │  -g2012) │   │          │   │          │    │
│  │ code     │   └────┬─────┘   └────┬─────┘   └────┬─────┘    │
│  └──────────┘        │              │               │           │
│       ▲              │              │               │           │
│       │         ┌────▼─────┐  ┌────▼─────┐   ┌────▼─────┐    │
│       │         │ FAIL:    │  │ FAIL:    │   │ PASS:    │    │
│       │         │ Syntax   │  │ Mismatch │   │ All      │    │
│       │         │ errors   │  │ or assert│   │ checks   │    │
│       │         │ in stderr│  │ failures │   │ passed   │    │
│       │         └────┬─────┘  └────┬─────┘   └────┬─────┘    │
│       │              │              │               │           │
│       │         ┌────▼──────────────▼─────┐   ┌────▼─────┐    │
│       │         │ Judge formats error:    │   │ Mark     │    │
│       │         │ - Error type            │   │ block    │    │
│       │         │ - File:line:col         │   │ PASSED   │    │
│       │         │ - Full stderr context   │   │ in state │    │
│       │         │ - Failing test vector   │   └──────────┘    │
│       │         │ - Previous attempt #    │                    │
│       │         └────┬────────────────────┘                    │
│       │              │                                          │
│       └──────────────┘                                          │
│         (retry with structured error context)                   │
│         Max 8 retries → human escalation                        │
└─────────────────────────────────────────────────────────────────┘
```

**Concrete Scenario — Compilation Failure:**

1. The RTL Engineer writes `ofdm_modulator.sv`.
2. The Judge invokes: `iverilog -g2012 -Wall -o ofdm_mod_tb ofdm_modulator.sv ofdm_mod_tb.sv`
3. Compilation fails. The Judge captures stderr:
   ```
   ofdm_modulator.sv:47: error: Unable to bind wire/reg `subcarrier_idx` 
   in `ofdm_modulator.fft_stage`
   ofdm_modulator.sv:112: error: Unknown module type: butterfly_unit
   ```
4. The Judge classifies the errors:
   - Error 1: **Undeclared signal** — `subcarrier_idx` referenced but not declared in scope `fft_stage`
   - Error 2: **Missing module** — `butterfly_unit` instantiated but not defined
5. The Judge constructs a structured error payload:

```json
{
  "block": "ofdm_modulator",
  "attempt": 2,
  "status": "COMPILE_FAIL",
  "errors": [
    {
      "type": "UNDECLARED_SIGNAL",
      "file": "ofdm_modulator.sv",
      "line": 47,
      "signal": "subcarrier_idx",
      "scope": "fft_stage",
      "suggestion": "Declare `logic [10:0] subcarrier_idx` in the fft_stage generate block or pass as port"
    },
    {
      "type": "MISSING_MODULE",
      "file": "ofdm_modulator.sv",
      "line": 112,
      "module": "butterfly_unit",
      "suggestion": "Either define butterfly_unit as a submodule in a separate file or inline the logic"
    }
  ],
  "full_stderr": "<raw stderr text>",
  "previous_code_hash": "a3f8c2..."
}
```

6. The Orchestrator routes this back to the RTL Engineer with the prompt: *"Your previous ofdm_modulator.sv (attempt 2) failed compilation. Here are the structured errors: {error_payload}. Here is your previous code: {previous_code}. Fix ONLY the identified errors. Do not refactor unrelated logic."*

7. The RTL Engineer produces a corrected version. The loop repeats.

**Simulation Failure (Functional Mismatch):**

If compilation succeeds but simulation shows a mismatch between DUT output and the Python reference model's golden vectors, the Judge:

1. Identifies the first diverging output sample (cycle number, expected vs. actual value)
2. Extracts the input stimulus that produced the mismatch
3. Asks the 3GPP Librarian to confirm the expected behavior for that input
4. Sends the RTL Engineer: the failing test vector, expected output, actual output, and the relevant spec excerpt

### 1.5 Cost Analysis (Cloud Plan A)

**Assumptions:**
- Target design: ~15 RTL modules, ~15 testbenches, ~30 config files
- Average 3 iterations per module to pass verification
- Context window usage: ~50K tokens input, ~8K tokens output per agent call

| Cost Component | Tokens (M) | Unit Cost | Total |
|---|---|---|---|
| Orchestrator (Sonnet) — ~500 calls | 25M in / 2M out | $3/M in, $15/M out | $105 |
| 3GPP Librarian (Sonnet + RAG) — ~300 calls | 15M in / 3M out | $3/M in, $15/M out | $90 |
| RTL Engineer (Opus) — ~200 calls | 20M in / 4M out | $15/M in, $75/M out | $600 |
| RTL Engineer (o3 fallback) — ~30 calls | 6M in / 1.5M out | $10/M in, $40/M out | $120 |
| Verification Eng (Opus) — ~150 calls | 15M in / 5M out | $15/M in, $75/M out | $600 |
| Judge (Sonnet) — ~600 calls | 12M in / 1M out | $3/M in, $15/M out | $51 |
| PnR Lead (Sonnet) — ~100 calls | 5M in / 1M out | $3/M in, $15/M out | $30 |
| Embedding (Voyage) — one-time ingest | 10M tokens | $0.06/M | $0.60 |
| Pinecone (serverless) — 1 month | — | ~$70/mo | $70 |
| **Total Estimated** | | | **~$1,670** |

**Cost Control Mechanisms:**
- The Orchestrator tracks `token_budget_remaining` in state. When budget reaches 20%, it switches the RTL Engineer from Opus to Sonnet for simpler modules (register file configs, simple FSMs).
- Caching: LangSmith's prompt caching deduplicates the 3GPP Librarian's system prompt + few-shot examples (~8K tokens saved per call).
- The Judge agent uses Sonnet, not Opus — it only parses logs, it doesn't generate code.

---

<a name="part-ii"></a>
## Part II: The "Sovereign" Local Stack (Plan B)

### 2.1 Hardware Budget

| Component | Spec | Role |
|---|---|---|
| GPU | NVIDIA RTX 4090 (24 GB VRAM) | Model inference (primary) |
| System RAM | 64 GB DDR5 | Vector DB, model overflow (CPU offload layers), compilation |
| Storage | 1 TB NVMe SSD | Models, specs, artifacts, vector DB |
| CPU | AMD Ryzen 9 / Intel i9 (16+ cores) | iverilog, Verilator, OpenLane, parallel tasks |

**Alternative:** Mac Studio M2 Ultra (76 GB unified memory) — runs all models in unified memory without VRAM constraints, but at ~60% the throughput of the RTX 4090 for GGUF inference via `llama.cpp`.

### 2.2 Model Selection (Local)

| Agent Role | Model | Quantization | VRAM Usage | Rationale |
|---|---|---|---|---|
| **RTL Engineer** | `DeepSeek-R1-Distill-Qwen-32B` | EXL2 4.5bpw | ~18 GB | Best local model for SystemVerilog. The R1 distillation retains chain-of-thought reasoning critical for correct RTL. 32B is the largest model that fits in 24 GB VRAM at usable quantization. Significantly outperforms CodeLlama-34B on Verilog benchmarks (VerilogEval). |
| **Verification Engineer** | `DeepSeek-R1-Distill-Qwen-32B` | EXL2 4.5bpw | ~18 GB (shared, sequential) | Same model, different system prompt. Sequential execution with RTL Engineer — they never run simultaneously. |
| **3GPP Librarian** | `Qwen2.5-14B-Instruct` | GGUF Q5_K_M | ~10 GB | RAG-focused role needs strong instruction following and retrieval synthesis, not code generation. 14B fits comfortably and leaves room for vector DB in RAM. Qwen2.5 excels at structured extraction and document QA. |
| **Orchestrator + Judge** | `Qwen2.5-7B-Instruct` | GGUF Q6_K | ~6 GB | Lightweight routing and log parsing. Does not generate code. 7B is sufficient for structured JSON output and error classification. Runs on CPU via `llama.cpp` while GPU handles the heavy model. |
| **PnR Lead** | `Qwen2.5-7B-Instruct` | GGUF Q6_K | ~6 GB (shared with Orchestrator) | Templated config generation. Minimal reasoning required. |

**Why DeepSeek-R1-Distill over alternatives:**

- **vs. CodeLlama-34B-Instruct:** CodeLlama was not trained on reasoning traces. It generates plausible-looking Verilog but makes systematic errors with sequential logic timing (e.g., incorrect reset behavior, missing clock enable conditions). DeepSeek-R1's distilled reasoning chain explicitly "thinks through" the state machine transitions before emitting code.
- **vs. Llama-3.1-70B (quantized):** At Q3_K_M quantization (to fit 24 GB), the 70B model's quality degrades substantially — especially on structured output tasks. The 32B model at 4.5bpw retains >95% of its FP16 accuracy.
- **vs. DeepSeek-Coder-V2-Lite (16B):** Too small for complex multi-block RTL with large contexts. Struggles with the long interface specifications needed for AXI-Stream interconnect.

**Inference Stack:**

- **Primary (GPU):** `exllamav2` server with `DeepSeek-R1-Distill-Qwen-32B` loaded at EXL2 4.5bpw. Context length: 16,384 tokens (sufficient for single-module generation with spec context). Temperature: `0.1` for RTL generation (near-deterministic), `0.3` for testbench generation (slight creativity for constrained-random).
- **Secondary (CPU):** `llama.cpp` server (`llama-server`) running `Qwen2.5-7B-Instruct-Q6_K.gguf` on CPU threads. Used for Orchestrator and Judge — these are latency-tolerant (a few seconds per call is fine).
- **Librarian (GPU, time-shared):** When the RTL Engineer is not active (e.g., during compilation/simulation), the GPU is released and `Qwen2.5-14B-Instruct-Q5_K_M.gguf` is loaded for RAG queries. Model swapping via `exllamav2`'s dynamic model loading takes ~8 seconds from NVMe.

**Memory Layout (steady state during RTL generation):**

```
GPU VRAM (24 GB):
├── DeepSeek-R1-32B EXL2 4.5bpw    — 18 GB
├── KV cache (16K context)          — 4 GB
└── CUDA overhead                   — 2 GB

System RAM (64 GB):
├── OS + tools (iverilog, verilator, openlane)  — 8 GB
├── ChromaDB vector store (3GPP specs)          — 3 GB
├── llama.cpp (Qwen-7B Q6_K on CPU)            — 6 GB
├── Python orchestration (LangGraph)            — 2 GB
└── Available for compilation/simulation         — 45 GB
```

### 2.3 Tooling & Infrastructure (Local)

#### 2.3.1 Vector Database: ChromaDB (Embedded Mode)

**Why ChromaDB over FAISS:**
FAISS is a vector similarity library, not a database. It lacks: metadata filtering, persistent storage, and built-in document management. ChromaDB provides all three with <500 MB RAM overhead in embedded mode. For our corpus of ~50,000 chunks (4 spec documents × ~5,000 pages), ChromaDB's HNSW index consumes ~2.5 GB on disk and ~3 GB in RAM.

**Embedding Model (Local):** `nomic-embed-text-v1.5` (137M parameters, GGUF quantized). Runs on CPU. Produces 768-dim embeddings. Benchmarks within 3% of Voyage-3 on MTEB retrieval tasks at 1/100th the cost. Invoked via `llama.cpp`'s embedding endpoint.

**Chunking:** Identical hierarchical strategy as Plan A (Section 1.3.2), but with a reduced chunk size of 384 tokens (to compensate for the smaller 16K context window of local models — we need to fit more retrieved chunks + the task prompt).

#### 2.3.2 Orchestration: LangGraph (Local)

LangGraph runs locally as a Python process. State is persisted to SQLite (instead of PostgreSQL) via LangGraph's `SqliteSaver`. The graph topology is identical to Plan A. The only difference is that `dispatch_agent()` calls local HTTP endpoints (`http://localhost:8001` for exllamav2, `http://localhost:8002` for llama.cpp) instead of cloud APIs.

#### 2.3.3 EDA Tool Integration via Model Context Protocol (MCP)

The key integration challenge for Plan B is connecting the LLM to local EDA tools. We use **MCP (Model Context Protocol)** to expose iverilog, Verilator, and OpenLane as tool endpoints.

**MCP Server Definitions:**

```json
{
  "mcpServers": {
    "iverilog": {
      "command": "python",
      "args": ["/opt/mcp-servers/iverilog_server.py"],
      "description": "Compile and simulate SystemVerilog via Icarus Verilog"
    },
    "verilator": {
      "command": "python",
      "args": ["/opt/mcp-servers/verilator_server.py"],
      "description": "Lint, compile, and simulate via Verilator"
    },
    "openlane": {
      "command": "python",
      "args": ["/opt/mcp-servers/openlane_server.py"],
      "description": "Run RTL-to-GDSII flow via OpenLane 2"
    },
    "waveform_viewer": {
      "command": "python",
      "args": ["/opt/mcp-servers/vcd_analyzer.py"],
      "description": "Parse VCD waveforms and extract signal values at specific cycles"
    }
  }
}
```

**Example: iverilog MCP Tool Schema:**

```python
# iverilog_server.py — MCP tool exposed to agents
@mcp_tool(name="compile_verilog")
def compile_verilog(
    source_files: list[str],   # e.g., ["ofdm_mod.sv", "ofdm_mod_tb.sv"]
    top_module: str,            # e.g., "ofdm_mod_tb"
    output_binary: str = "sim.out",
    defines: dict[str, str] = {},  # e.g., {"SIM": "1", "NUM_SUBCARRIERS": "1024"}
) -> dict:
    """Compiles SystemVerilog files using iverilog -g2012. Returns
    {'success': bool, 'stdout': str, 'stderr': str}"""
    cmd = ["iverilog", "-g2012", "-Wall", "-o", output_binary]
    for k, v in defines.items():
        cmd += [f"-D{k}={v}"]
    cmd += source_files
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr
    }

@mcp_tool(name="run_simulation")
def run_simulation(
    binary: str = "sim.out",
    timeout_seconds: int = 120,
    vcd_output: str = "dump.vcd"
) -> dict:
    """Runs a compiled iverilog simulation. Returns
    {'success': bool, 'stdout': str, 'stderr': str, 'vcd_path': str}"""
    result = subprocess.run(
        ["vvp", binary],
        capture_output=True, text=True, timeout=timeout_seconds
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "vcd_path": vcd_output if os.path.exists(vcd_output) else None
    }
```

**OpenLane Integration:**

OpenLane 2 is installed via Nix (the recommended method). The MCP server wraps its CLI:

```python
@mcp_tool(name="run_openlane_flow")
def run_openlane_flow(
    design_dir: str,
    config_file: str = "config.json",
    steps: list[str] = None  # e.g., ["Synthesis", "Floorplan", "Placement"]
) -> dict:
    """Runs OpenLane 2 flow. Returns synthesis/PnR report summaries."""
    cmd = ["openlane", "--design-dir", design_dir, config_file]
    if steps:
        cmd += ["--to", steps[-1]]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    # Parse reports from design_dir/runs/latest/
    reports = parse_openlane_reports(f"{design_dir}/runs/latest/")
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout[-2000:],  # Last 2K chars
        "stderr": result.stderr[-2000:],
        "reports": reports
    }
```

### 2.4 Judge Mechanism (Local)

Identical to Plan A's loop (Section 1.4), with one key adaptation: the Judge runs on the CPU-bound Qwen-7B model. Since error parsing is a structured extraction task (not creative generation), the 7B model performs adequately. The structured error JSON schema is enforced using `llama.cpp`'s grammar-constrained generation (GBNF grammar) to guarantee valid JSON output:

```gbnf
root   ::= "{" ws "\"block\":" ws string "," ws "\"attempt\":" ws number "," ws 
           "\"status\":" ws status "," ws "\"errors\":" ws "[" error-list "]" ws "}"
status ::= "\"COMPILE_FAIL\"" | "\"SIM_FAIL\"" | "\"SIM_PASS\"" | "\"LINT_WARN\""
error-list ::= error ("," ws error)*
error  ::= "{" ws "\"type\":" ws string "," ws "\"file\":" ws string "," ws 
           "\"line\":" ws number "," ws "\"message\":" ws string ws "}"
```

This eliminates the failure mode where a small model outputs malformed JSON that breaks the orchestration loop.

---

<a name="part-iii"></a>
## Part III: Step-by-Step Execution Roadmap

### Phase 1: Architecture & Specification Extraction

**Objective:** Decompose the 4G LTE baseband into implementable RTL blocks with precise spec-derived requirements.

**Input:**
- 3GPP TS 36.211 (v17.0.0) — Physical Channels and Modulation
- 3GPP TS 36.212 (v17.0.0) — Multiplexing and Channel Coding
- 3GPP TS 36.213 (v17.0.0) — Physical Layer Procedures
- 3GPP TS 36.300 (v17.0.0) — E-UTRAN Overall Description
- Design constraints: Cat-1 UE, 10 MHz bandwidth (50 RBs), FDD, single antenna port, DL only (simplification)

**Step 1.1 — Spec Ingestion (Automated, One-Time)**

| Action | Plan A | Plan B |
|---|---|---|
| Parse PDFs | `pdfplumber` + `Nougat` on cloud VM | `pdfplumber` + `Nougat` on local CPU (slow but one-time, ~2 hours) |
| Chunk & embed | `voyage-3-large` API | `nomic-embed-text-v1.5` local GGUF, ~4 hours |
| Store | Pinecone serverless | ChromaDB embedded, SQLite-backed |
| Validate | Sample 50 queries, check retrieval precision | Same |

**Step 1.2 — Block Decomposition (Orchestrator + Librarian)**

The Orchestrator prompts the 3GPP Librarian with: *"Enumerate all functional blocks required for a Cat-1 UE downlink baseband data path. For each block, cite the governing 3GPP section and list the input/output data types."*

**Output — Block Registry:**

| Block Name | 3GPP Section | Function | Interfaces |
|---|---|---|---|
| `resource_demapper` | TS 36.211 §6.3 | Extracts data symbols from OFDM resource grid | In: OFDM grid (freq×time matrix), Out: QAM symbols stream |
| `channel_estimator` | TS 36.211 §6.10 | Estimates channel from CRS pilot symbols | In: received OFDM grid, Out: channel estimate matrix |
| `equalizer` | TS 36.213 §7.1 | MMSE/ZF equalization per subcarrier | In: data symbols + channel estimate, Out: equalized symbols |
| `qam_demodulator` | TS 36.211 §7.1 | Soft demapping (LLR computation) | In: equalized symbols + modulation order, Out: soft bits (LLRs) |
| `descrambler` | TS 36.211 §7.2 | Gold-sequence descrambling | In: soft bits + scrambling init, Out: descrambled soft bits |
| `rate_dematcher` | TS 36.212 §5.1.4 | Circular buffer rate de-matching | In: descrambled bits + code rate params, Out: systematic+parity bits |
| `turbo_decoder` | TS 36.212 §5.1.3 | Max-Log-MAP turbo decoding (8 iterations) | In: systematic+parity LLRs, Out: hard decoded bits |
| `crc_checker` | TS 36.212 §5.1.1 | CRC-24A verification on transport block | In: decoded bits, Out: pass/fail + payload |
| `ofdm_demodulator` | TS 36.211 §6.12 | CP removal + FFT (1024-point for 10MHz) | In: time-domain IQ samples, Out: freq-domain OFDM grid |
| `dl_sch_processor` | TS 36.300 §11.2 | Transport channel → MAC PDU extraction | In: decoded transport blocks, Out: MAC PDUs |

**Step 1.3 — Interface Specification**

The Orchestrator instructs the RTL Engineer to define a global `lte_baseband_pkg.sv` package containing:
- AXI-Stream parameter definitions (DATA_WIDTH, USER_WIDTH)
- Enumerated types for modulation order, CP type, bandwidth
- Struct types for resource allocation (RB start, RB count, MCS index)

**Output Artifact:** `lte_baseband_pkg.sv`, `block_registry.json`

---

### Phase 2: RTL Design

**Objective:** Generate synthesizable SystemVerilog for each block.

**Execution Order:** Bottom-up by data-path dependency:
1. `ofdm_demodulator` (pure signal processing, no spec-protocol dependencies)
2. `channel_estimator` + `equalizer` (signal processing)
3. `qam_demodulator` + `descrambler` (bit-level processing)
4. `rate_dematcher` (protocol-defined interleaving — spec-heavy)
5. `turbo_decoder` (algorithmically complex — may trigger o3 fallback in Plan A)
6. `crc_checker` (simple but correctness-critical)
7. `resource_demapper` + `dl_sch_processor` (control-path integration)
8. `lte_baseband_top` (top-level interconnect)

**Per-Block Flow:**

For each block `B`:

1. **Orchestrator** retrieves the spec context for `B` from the Librarian.
2. **Orchestrator** sends the RTL Engineer a task:
   ```
   Generate SystemVerilog for block: {B.name}
   3GPP Reference: {B.spec_sections}
   Specification Details: {librarian_output}
   Upstream Interface: {B.input_interface from lte_baseband_pkg}
   Downstream Interface: {B.output_interface}
   Constraints: Synthesizable, 100 MHz target, AXI-Stream ports,
   synchronous active-low reset.
   ```
3. **RTL Engineer** generates `{B.name}.sv`. Writes to artifact store.
4. Control passes to Phase 3 (Verification) for this block immediately — **blocks are verified incrementally, not all at once**.

**Input:** `block_registry.json`, `lte_baseband_pkg.sv`, spec excerpts from Librarian
**Output:** One `.sv` file per block (10 files), `lte_baseband_top.sv`

---

### Phase 3: Verification

**Objective:** Functionally verify every block against 3GPP-derived golden vectors.

**Per-Block Flow:**

1. **Orchestrator** tasks the Verification Engineer:
   ```
   Create a testbench for: {B.name}
   RTL Source: {B.sv file contents}
   Specification: {spec_excerpts}
   Requirements:
   - iverilog-compatible self-checking testbench
   - Python reference model generating golden vectors
   - Test cases: {specific test vectors derived from spec examples,
     e.g., TS 36.211 Annex A reference signals}
   ```

2. **Verification Engineer** produces:
   - `{B.name}_tb.sv` — iverilog testbench
   - `{B.name}_ref.py` — Python reference model
   - `golden_vectors/{B.name}_stim.hex`, `golden_vectors/{B.name}_expected.hex`

3. **Judge** executes the verification pipeline:
   ```bash
   # Step 1: Generate golden vectors
   python {B.name}_ref.py --output golden_vectors/

   # Step 2: Compile
   iverilog -g2012 -Wall -I ../rtl -o {B.name}_sim \
     ../rtl/lte_baseband_pkg.sv ../rtl/{B.name}.sv {B.name}_tb.sv

   # Step 3: Simulate
   vvp {B.name}_sim +vcd={B.name}.vcd
   ```

4. **Judge** evaluates:
   - **Compilation failed?** → Parse stderr, construct error payload, route to RTL Engineer for fix.
   - **Simulation assertion failed?** → Extract failing cycle, expected vs. actual, route to RTL Engineer with spec context.
   - **All tests passed?** → Mark block as `PASSED` in `DesignState.block_registry`.

5. **Regression:** When a block is modified (due to interface changes from a dependent block), the Judge re-runs all previously passing testbenches to catch regressions.

**Input:** RTL `.sv` files, spec excerpts
**Output:** Testbenches (`.sv`), reference models (`.py`), golden vectors (`.hex`), VCD waveforms, pass/fail status per block

---

### Phase 4: Physical Design (RTL-to-GDSII)

**Objective:** Synthesize and place-and-route the verified RTL using the open-source OpenLane 2 flow targeting SkyWater SKY130.

**Step 4.1 — Synthesis Configuration**

The PnR Lead generates `config.json` for each macro:

```json
{
  "DESIGN_NAME": "lte_baseband_top",
  "VERILOG_FILES": ["dir::rtl/*.sv"],
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 10.0,
  "FP_CORE_UTIL": 40,
  "PL_TARGET_DENSITY": 0.55,
  "SYNTH_STRATEGY": "DELAY 3",
  "STA_REPORT_POWER": true,
  "PDK": "sky130A",
  "STD_CELL_LIBRARY": "sky130_fd_sc_hd",
  "MAX_FANOUT_CONSTRAINT": 10,
  "ROUTING_CORES": 8
}
```

**Step 4.2 — Iterative PnR Loop**

```
Synthesis (Yosys) → Floorplan → Placement → CTS → Routing → DRC/LVS → Signoff
     │                                                            │
     │                    ┌──────────────┐                        │
     └────────────────────│ PnR Lead     │◀───────────────────────┘
                          │ Interprets   │
                          │ reports,     │
                          │ adjusts      │
                          │ config       │
                          └──────────────┘
```

The PnR Lead reads:
- `synthesis_stats.rpt` — gate count, area
- `sta_summary.rpt` — worst negative slack (WNS), total negative slack (TNS)
- `drc_summary.rpt` — DRC violation count and types
- `antenna_summary.rpt` — antenna rule violations

**Decision Logic:**
- WNS < -0.5 ns → Increase `CLOCK_PERIOD` or insert pipeline stages (escalate to RTL Engineer)
- Core utilization > 80% → Reduce `FP_CORE_UTIL` to alleviate congestion
- DRC violations > 100 → Increase routing tracks, adjust PDN configuration
- If synthesis area exceeds die budget → flag for RTL optimization (e.g., resource sharing in turbo decoder)

**Input:** Verified RTL (`.sv` files), OpenLane installation, SKY130 PDK
**Output:** Gate-level netlist, `GDSII` layout, timing/area/power reports, DRC-clean signoff

---

<a name="appendix-a"></a>
## Appendix A: Complete Agent System Prompt Templates

### A.1 Orchestrator — Full System Prompt

```
<system>
You are the Silicon-Agent Orchestrator managing the design of an open-source
4G LTE Cat-1 UE downlink baseband modem.

ROLE: You are a project manager. You NEVER write RTL, testbenches, or
configurations yourself. You coordinate specialist agents.

STATE: You have access to a DesignState object with these fields:
- project_phase: current phase (spec/rtl/verify/pnr)
- block_registry: dict mapping block names to status
  (NOT_STARTED / IN_PROGRESS / FAILING / PASSED / PNR_DONE)
- iteration_counts: dict mapping block names to retry counts
- error_log: list of structured error records

DECISION RULES:
1. ALWAYS prioritize FAILING blocks over starting new blocks.
2. If iteration_count for any block exceeds 8, escalate to human queue.
3. When all blocks in block_registry are PASSED, transition to PnR phase.
4. During PnR, if timing violations require RTL changes, transition the
   affected block back to FAILING and re-enter the verify loop.
5. Issue ONE agent task per turn. Wait for completion before issuing the next.

OUTPUT FORMAT: Always respond with valid JSON:
{
  "action": "dispatch" | "escalate" | "phase_transition" | "complete",
  "target_agent": "librarian" | "rtl_engineer" | "verification_engineer" |
                  "judge" | "pnr_lead",
  "task": "<precise natural-language task description>",
  "context": { <relevant state fields and artifacts> },
  "state_updates": { <fields to update after completion> }
}
</system>
```

### A.2 RTL Engineer — HDL-Specific Prompt Addendum

```
<coding_standards>
NAMING CONVENTIONS:
- Modules: snake_case (e.g., turbo_decoder, ofdm_demodulator)
- Signals: snake_case with suffix indicating type:
  _i (input port), _o (output port), _r (registered), _w (combinational wire)
  _valid, _ready, _data, _last (AXI-Stream signals)
- Parameters: UPPER_SNAKE_CASE (e.g., NUM_SUBCARRIERS, DATA_WIDTH)
- Generate blocks: named (e.g., gen_butterfly[i])

RESET STRATEGY:
- Synchronous, active-low reset (rst_n)
- All flip-flops must have explicit reset values
- Reset value for data paths: '0. Reset value for state machines: IDLE state.

INTERFACE TEMPLATE:
module <name> #(
  parameter int DATA_WIDTH = 32,
  parameter int USER_WIDTH = 8
)(
  input  logic                  clk,
  input  logic                  rst_n,
  // AXI-Stream input
  input  logic [DATA_WIDTH-1:0] s_axis_tdata_i,
  input  logic [USER_WIDTH-1:0] s_axis_tuser_i,
  input  logic                  s_axis_tvalid_i,
  output logic                  s_axis_tready_o,
  input  logic                  s_axis_tlast_i,
  // AXI-Stream output
  output logic [DATA_WIDTH-1:0] m_axis_tdata_o,
  output logic [USER_WIDTH-1:0] m_axis_tuser_o,
  output logic                  m_axis_tvalid_o,
  input  logic                  m_axis_tready_i,
  output logic                  m_axis_tlast_o
);
</coding_standards>
```

---

<a name="appendix-b"></a>
## Appendix B: Cost Model — Detailed Token Breakdown

### B.1 Per-Module Token Estimates

| Module | Spec Context (tokens) | RTL Output (tokens) | TB Output (tokens) | Avg Iterations | Total Tokens |
|---|---|---|---|---|---|
| `ofdm_demodulator` | 4,000 | 2,500 | 4,000 | 2 | 21,000 |
| `channel_estimator` | 6,000 | 3,000 | 5,000 | 3 | 42,000 |
| `equalizer` | 3,000 | 1,500 | 3,000 | 2 | 15,000 |
| `qam_demodulator` | 5,000 | 2,000 | 4,000 | 3 | 33,000 |
| `descrambler` | 2,000 | 800 | 2,000 | 1 | 4,800 |
| `rate_dematcher` | 8,000 | 4,000 | 6,000 | 4 | 72,000 |
| `turbo_decoder` | 10,000 | 6,000 | 8,000 | 5 | 120,000 |
| `crc_checker` | 1,500 | 600 | 1,500 | 1 | 3,600 |
| `resource_demapper` | 4,000 | 2,000 | 3,500 | 2 | 19,000 |
| `dl_sch_processor` | 5,000 | 2,500 | 4,000 | 3 | 34,500 |
| **Total** | | | | | **~365,000** |

(These are output tokens per module. Input tokens are ~3–5× output due to context injection.)

### B.2 Local Plan B — Inference Time Estimates

At ~30 tokens/second for DeepSeek-R1-32B on RTX 4090 (EXL2 4.5bpw, 16K context):

| Phase | Total Output Tokens | Estimated Wall Time |
|---|---|---|
| Spec extraction & block decomposition | ~20,000 | ~11 minutes |
| RTL generation (all modules, all iterations) | ~150,000 | ~83 minutes |
| Testbench generation (all modules, all iterations) | ~200,000 | ~111 minutes |
| Judge evaluations | ~50,000 | ~28 minutes (on CPU, ~10 tok/s) |
| PnR configuration | ~10,000 | ~6 minutes |
| **Total LLM inference time** | **~430,000** | **~4 hours** |

Note: Compilation, simulation, and PnR execution time is **additional** — OpenLane synthesis alone may take 2–6 hours for a design of this complexity on a 16-core CPU.

---

<a name="appendix-c"></a>
## Appendix C: 3GPP Spec Ingestion Pipeline — Full Implementation

### C.1 PDF Processing Script

```python
#!/usr/bin/env python3
"""3GPP Specification Ingestion Pipeline for Silicon-Agent."""

import pdfplumber
import re
import json
from pathlib import Path
from dataclasses import dataclass, asdict

@dataclass
class SpecChunk:
    spec_id: str          # e.g., "36.211"
    section_path: str     # e.g., "6.3.1"
    section_title: str    # e.g., "OFDM baseband signal generation"
    content_type: str     # "prose" | "table" | "equation"
    text: str
    page_number: int
    chunk_index: int

HEADING_PATTERN = re.compile(
    r'^(\d+(?:\.\d+)*)\s+(.+)$', re.MULTILINE
)

def extract_sections(pdf_path: str, spec_id: str) -> list[SpecChunk]:
    chunks = []
    chunk_idx = 0
    
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        page_map = []  # (char_offset, page_number)
        
        for i, page in enumerate(pdf.pages):
            offset = len(full_text)
            page_text = page.extract_text() or ""
            full_text += page_text + "\n"
            page_map.append((offset, i + 1))
            
            # Extract tables separately
            for table in page.extract_tables():
                if table and len(table) > 1:
                    table_text = format_table(table)
                    chunks.append(SpecChunk(
                        spec_id=spec_id,
                        section_path="",  # Will be assigned by nearest heading
                        section_title="Table",
                        content_type="table",
                        text=table_text,
                        page_number=i + 1,
                        chunk_index=chunk_idx
                    ))
                    chunk_idx += 1
        
        # Split by headings
        headings = list(HEADING_PATTERN.finditer(full_text))
        for i, match in enumerate(headings):
            start = match.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(full_text)
            section_text = full_text[start:end].strip()
            
            # Sub-chunk if > 512 tokens (~2048 chars)
            for sub_chunk in split_with_overlap(section_text, max_chars=2048, overlap=512):
                page_num = get_page_number(match.start(), page_map)
                chunks.append(SpecChunk(
                    spec_id=spec_id,
                    section_path=match.group(1),
                    section_title=match.group(2).strip(),
                    content_type="prose",
                    text=sub_chunk,
                    page_number=page_num,
                    chunk_index=chunk_idx
                ))
                chunk_idx += 1
    
    return chunks

def split_with_overlap(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
    return chunks
```

### C.2 ChromaDB Ingest (Plan B)

```python
import chromadb
from chromadb.config import Settings

client = chromadb.PersistentClient(
    path="/data/vectordb/3gpp",
    settings=Settings(anonymized_telemetry=False)
)

collection = client.get_or_create_collection(
    name="3gpp_lte_specs",
    metadata={"hnsw:space": "cosine", "hnsw:M": 32, "hnsw:ef_construction": 200}
)

# Embed using local nomic model via llama.cpp endpoint
import requests

def embed_local(texts: list[str]) -> list[list[float]]:
    resp = requests.post("http://localhost:8003/embedding", json={
        "content": texts
    })
    return resp.json()["results"]

# Batch ingest
for batch in batched(all_chunks, batch_size=64):
    embeddings = embed_local([c.text for c in batch])
    collection.add(
        ids=[f"{c.spec_id}_{c.section_path}_{c.chunk_index}" for c in batch],
        documents=[c.text for c in batch],
        embeddings=embeddings,
        metadatas=[{
            "spec_id": c.spec_id,
            "section_path": c.section_path,
            "section_title": c.section_title,
            "content_type": c.content_type,
            "page_number": c.page_number
        } for c in batch]
    )
```

### C.3 Retrieval Query Example

```python
def query_specs(question: str, spec_filter: str = None, top_k: int = 10):
    """Query the 3GPP spec vector store."""
    query_embedding = embed_local([question])[0]
    
    where_filter = None
    if spec_filter:
        where_filter = {"spec_id": {"$eq": spec_filter}}
    
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where_filter,
        include=["documents", "metadatas", "distances"]
    )
    
    return [{
        "text": doc,
        "metadata": meta,
        "relevance": 1 - dist  # cosine distance to similarity
    } for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0]
    )]
```

---

## Summary Comparison: Plan A vs Plan B

| Dimension | Plan A (Cloud) | Plan B (Local) |
|---|---|---|
| **Total Cost** | ~$1,670 (API + infra) | ~$0 marginal (hardware amortized) |
| **Wall-Clock Time** | ~6–10 hours (parallel agents, fast inference) | ~24–48 hours (sequential, slower inference + PnR) |
| **RTL Quality** | Highest (Opus + o3 for hard blocks) | Good (R1-32B is strong but occasionally needs more iterations) |
| **Max Context** | 200K tokens (Opus) | 16K tokens (exllamav2) — requires more aggressive chunking |
| **Verification Loop Speed** | ~15 seconds per Judge cycle | ~45 seconds per Judge cycle |
| **Spec Retrieval Quality** | Superior (Voyage-3 + Cohere reranker) | Good (nomic-embed + ChromaDB native ranking) |
| **Offline Capability** | No — requires internet | Yes — fully air-gapped after setup |
| **Reproducibility** | API versions may drift | Fully reproducible (pinned model weights + quantizations) |

---

*This document constitutes the complete architectural blueprint for the Silicon-Agent workflow. It is designed to be directly implementable by a team with access to the specified hardware and software. No Verilog code is included — only the plan to have agents write it.*

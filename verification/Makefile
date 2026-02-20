# ==========================================
# Directory Context: Design Verification (DV)
# Note to Agents: 'dv' refers to Design Verification. This Makefile 
# is used by the Verification Judge to compile the SystemVerilog RTL 
# and execute the Python Golden Models (cocotb) against it.
# ==========================================

# Define the simulator (Icarus Verilog is default for our Judge)
SIM ?= icarus
TOPLEVEL_LANG ?= verilog

# Define the path to the RTL directory (one level up)
RTL_DIR = ../rtl

# Provide the SystemVerilog source files
# (If the RTL Architect splits this into multiple files, add them here)
VERILOG_SOURCES += $(RTL_DIR)/lte_pss_gen.sv

# TOPLEVEL is the name of the top-level SystemVerilog module
TOPLEVEL = lte_pss_gen

# MODULE is the basename of the Python test file (without .py)
MODULE = tb_lte_pss_gen

# Tell Icarus Verilog to support SystemVerilog-2012 syntax 
# (Required by our RTL Architect's System Prompt)
COMPILE_ARGS += -g2012

# Include cocotb's standard simulation makefile rules
include $(shell cocotb-config --makefiles)/Makefile.sim

# Custom clean target to sweep up simulation artifacts
clean::
	@rm -rf sim_build
	@rm -f results.xml
	@rm -rf __pycache__
	@rm -f *.vcd
	@rm -f *.fst
import os
import numpy as np
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly

# ==========================================
# 1. 3GPP LTE PSS Golden Model (Numpy)
# ==========================================
def generate_pss_golden(n_id_2: int, bit_width: int = 16) -> np.ndarray:
    """
    Generates the quantized 3GPP TS 36.211 PSS Zadoff-Chu sequence.
    Returns an array of complex integers (I + jQ).
    """
    # Map N_ID_2 to ZC root index (u)
    root_map = {0: 25, 1: 29, 2: 34}
    if n_id_2 not in root_map:
        raise ValueError("N_ID_2 must be 0, 1, or 2")
    
    u = root_map[n_id_2]
    n_zc = 63
    
    # Generate the length-62 sequence (DC punctured)
    n = np.arange(0, 62)
    # The 3GPP formula handles the DC puncture by shifting the index for the second half
    n_calc = np.where(n < 31, n, n + 1) 
    
    # Calculate floating point sequence
    phase = -np.pi * u * n_calc * (n_calc + 1) / n_zc
    pss_float = np.exp(1j * phase)
    
    # Quantize to fixed-point Q15 (assuming 16-bit signed I and Q)
    max_val = (2**(bit_width - 1)) - 1
    pss_i_quantized = np.round(np.real(pss_float) * max_val).astype(int)
    pss_q_quantized = np.round(np.imag(pss_float) * max_val).astype(int)
    
    return pss_i_quantized + 1j * pss_q_quantized

# ==========================================
# 2. Cocotb Hardware Verification Bench
# ==========================================
@cocotb.test()
async def test_lte_pss_generator(dut):
    """
    Drives the RTL, captures the AXI4-Stream output, and asserts EVM < 1%.
    """
    # Setup Clock (100 MHz)
    clock = Clock(dut.clk, 10, units="ns")
    cocotb.start_soon(clock.start())
    
    # Initialize inputs
    dut.rst_n.value = 0
    dut.m_axis_tready.value = 0
    dut.n_id_2.value = 0 # Testing sector 0 (u=25)
    
    # Reset sequence
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    
    # Generate Python Golden Reference
    golden_seq = generate_pss_golden(n_id_2=0, bit_width=16)
    
    # Assert TREADY to start pulling data from RTL
    dut.m_axis_tready.value = 1
    
    rtl_i_data = []
    rtl_q_data = []
    
    dut._log.info("Capturing AXI4-Stream data from RTL...")
    
    # Capture the 62 symbols
    while len(rtl_i_data) < 62:
        await RisingEdge(dut.clk)
        await ReadOnly() # Wait for signals to settle in this cycle
        
        if dut.m_axis_tvalid.value == 1 and dut.m_axis_tready.value == 1:
            # Assuming 32-bit tdata: [31:16] = Q, [15:0] = I
            raw_tdata = int(dut.m_axis_tdata.value)
            
            # Extract 16-bit signed integers (Two's complement handling)
            i_val = raw_tdata & 0xFFFF
            q_val = (raw_tdata >> 16) & 0xFFFF
            
            if i_val > 32767: i_val -= 65536
            if q_val > 32767: q_val -= 65536
            
            rtl_i_data.append(i_val)
            rtl_q_data.append(q_val)
            
            if dut.m_axis_tlast.value == 1:
                break
                
    dut._log.info(f"Captured {len(rtl_i_data)} symbols.")
    
    # ==========================================
    # 3. EVM (Error Vector Magnitude) Calculation
    # ==========================================
    rtl_complex = np.array(rtl_i_data) + 1j * np.array(rtl_q_data)
    
    error_vector = rtl_complex - golden_seq
    rms_error = np.sqrt(np.mean(np.abs(error_vector)**2))
    rms_reference = np.sqrt(np.mean(np.abs(golden_seq)**2))
    
    evm_pct = (rms_error / rms_reference) * 100
    
    dut._log.info(f"Calculated EVM: {evm_pct:.4f}%")
    
    # The Judge's Pass/Fail Criteria
    assert len(rtl_i_data) == 62, f"RTL generated {len(rtl_i_data)} symbols, expected 62."
    assert evm_pct < 1.0, f"EVM failed! Achieved {evm_pct:.2f}% (Limit: 1.0%)"
    dut._log.info("Golden Model Verification: PASSED")
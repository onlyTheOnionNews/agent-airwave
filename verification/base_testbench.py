"""
Base Verification Framework for Silicon Swarm 4G
Provides common utilities and base classes for LTE module verification.
"""

import numpy as np
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional


class LTEBaseTestbench(ABC):
    """
    Base class for LTE module verification testbenches.
    Provides common AXI4-Stream capture and EVM calculation utilities.
    """

    def __init__(self, dut, clk_period_ns: int = 10):
        self.dut = dut
        self.clk_period_ns = clk_period_ns
        self.clock = Clock(dut.clk, clk_period_ns, units="ns")
        cocotb.start_soon(self.clock.start())

    async def reset_sequence(self):
        """Standard reset sequence"""
        self.dut.rst_n.value = 0
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)
        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)

    async def capture_axis_data(self, expected_samples: int) -> Tuple[List[int], List[int]]:
        """
        Capture AXI4-Stream data from DUT.
        Assumes 32-bit tdata: [31:16] = Q, [15:0] = I
        Returns (i_data, q_data) lists
        """
        self.dut.m_axis_tready.value = 1

        i_data = []
        q_data = []

        self.dut._log.info(f"Capturing {expected_samples} AXI4-Stream samples...")

        while len(i_data) < expected_samples:
            await RisingEdge(self.dut.clk)
            await ReadOnly()

            if self.dut.m_axis_tvalid.value == 1 and self.dut.m_axis_tready.value == 1:
                raw_tdata = int(self.dut.m_axis_tdata.value)

                # Extract 16-bit signed integers
                i_val = raw_tdata & 0xFFFF
                q_val = (raw_tdata >> 16) & 0xFFFF

                # Two's complement handling
                if i_val > 32767: i_val -= 65536
                if q_val > 32767: q_val -= 65536

                i_data.append(i_val)
                q_data.append(q_val)

                if self.dut.m_axis_tlast.value == 1:
                    break

        self.dut._log.info(f"Captured {len(i_data)} samples.")
        return i_data, q_data

    def calculate_evm(self, rtl_i: List[int], rtl_q: List[int],
                     golden_i: np.ndarray, golden_q: np.ndarray) -> float:
        """
        Calculate Error Vector Magnitude (EVM) in percentage.
        """
        rtl_complex = np.array(rtl_i) + 1j * np.array(rtl_q)
        golden_complex = golden_i + 1j * golden_q

        error_vector = rtl_complex - golden_complex
        rms_error = np.sqrt(np.mean(np.abs(error_vector)**2))
        rms_reference = np.sqrt(np.mean(np.abs(golden_complex)**2))

        evm_pct = (rms_error / rms_reference) * 100
        return evm_pct

    @abstractmethod
    async def run_test(self):
        """Implement module-specific test logic"""
        pass


# ==========================================
# Module-Specific Testbenches
# ==========================================

class PSSGeneratorTestbench(LTEBaseTestbench):
    """
    Testbench for LTE Primary Synchronization Signal (PSS) Generator
    """

    def __init__(self, dut):
        super().__init__(dut)

    def generate_pss_golden(self, n_id_2: int, bit_width: int = 16) -> Tuple[np.ndarray, np.ndarray]:
        """
        Generates the quantized 3GPP TS 36.211 PSS Zadoff-Chu sequence.
        Returns (i_array, q_array) as separate real arrays.
        """
        # Map N_ID_2 to ZC root index (u)
        root_map = {0: 25, 1: 29, 2: 34}
        if n_id_2 not in root_map:
            raise ValueError("N_ID_2 must be 0, 1, or 2")

        u = root_map[n_id_2]
        n_zc = 63

        # Generate the length-62 sequence (DC punctured)
        n = np.arange(0, 62)
        n_calc = np.where(n < 31, n, n + 1)

        # Calculate floating point sequence
        phase = -np.pi * u * n_calc * (n_calc + 1) / n_zc
        pss_float = np.exp(1j * phase)

        # Quantize to fixed-point Q15
        max_val = (2**(bit_width - 1)) - 1
        pss_i_quantized = np.round(np.real(pss_float) * max_val).astype(int)
        pss_q_quantized = np.round(np.imag(pss_float) * max_val).astype(int)

        return pss_i_quantized, pss_q_quantized

    async def run_test(self):
        """Run PSS generator verification"""
        # Initialize inputs
        self.dut.m_axis_tready.value = 0
        self.dut.n_id_2.value = 0  # Testing sector 0 (u=25)

        await self.reset_sequence()

        # Generate golden reference
        golden_i, golden_q = self.generate_pss_golden(n_id_2=0, bit_width=16)

        # Capture RTL output
        rtl_i, rtl_q = await self.capture_axis_data(expected_samples=62)

        # Calculate EVM
        evm_pct = self.calculate_evm(rtl_i, rtl_q, golden_i, golden_q)
        self.dut._log.info(f"Calculated EVM: {evm_pct:.4f}%")

        # Verification criteria
        assert len(rtl_i) == 62, f"RTL generated {len(rtl_i)} symbols, expected 62."
        assert evm_pct < 1.0, f"EVM failed! Achieved {evm_pct:.2f}% (Limit: 1.0%)"
        self.dut._log.info("PSS Generator Verification: PASSED")


# ==========================================
# Test Runner Factory
# ==========================================

def get_testbench_for_module(module_name: str, dut) -> Optional[LTEBaseTestbench]:
    """
    Factory function to get the appropriate testbench for a module.
    The Verification Judge calls this based on the module being tested.
    """
    testbenches = {
        'lte_pss_gen': PSSGeneratorTestbench,
        # Add more modules here as the swarm designs them:
        # 'lte_sss_gen': SSSGeneratorTestbench,
        # 'lte_pbch': PBCHTestbench,
        # etc.
    }

    tb_class = testbenches.get(module_name)
    if tb_class:
        return tb_class(dut)
    else:
        dut._log.error(f"No testbench available for module: {module_name}")
        return None


# ==========================================
# Cocotb Test Entry Points
# ==========================================

@cocotb.test()
async def test_lte_pss_generator(dut):
    """Entry point for PSS generator testing"""
    tb = PSSGeneratorTestbench(dut)
    await tb.run_test()

# Future test entry points can be added here:
# @cocotb.test()
# async def test_lte_sss_generator(dut):
#     tb = SSSGeneratorTestbench(dut)
#     await tb.run_test()
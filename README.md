# RV32I Pipeline Hazard Detection Simulator

A simulator built to detect and handle data hazards in a 5-stage RV32I pipeline.

## What It Does

This project simulates how a processor pipeline detects Read After Write (RAW) hazards — situations where one instruction needs to read a register that a previous instruction hasn't finished writing yet. When a hazard is detected, the pipeline stalls to wait for the value to be available before continuing.

## How to Run

```
python Comp_Arch_Project.py
```

Put your instructions in `hex_inst.txt` (one 32-bit hex instruction per line) and run the command above.

## Hazard Detection

The simulator checks for RAW hazards at the ID stage by comparing the current instruction's source registers (rs1, rs2) against the destination registers of instructions currently in the EX, MEM, and WB stages.

If a hazard is found:
- A bubble (NOP) is inserted into the pipeline
- The PC is held so the stalled instruction re-executes next cycle
- A stall counter is incremented

## What Causes a Stall

A stall happens when an instruction reads a register that a previous instruction is still in the process of writing. For example:

```
addi x1, x0, 5    # writes x1
addi x2, x1, 3    # reads x1 — stall! x1 not written yet
```

Instructions that only read from x0 never cause stalls since x0 is hardwired to zero and never written.

## Output

After running, the simulator prints the total cycles, number of stalls, and CPI (Cycles Per Instruction).

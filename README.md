
The purpose of this project is to develop a simulator built to detect and handle data hazards in a 5-stage RV32I pipeline.



This project simulates how a processor pipeline detects Read After Write (RAW) hazards — situations where one instruction needs to read a register that a previous instruction hasn't finished writing yet. When a hazard is detected, the pipeline stalls to wait for the value to be available before continuing.

The command to run this program is
python Comp_Arch_Project.py


Paste your instructions into hex_inst.txt from the instructions document and run the command above.



The simulator checks for RAW hazards at the ID stage by comparing the current instruction's source registers (rs1, rs2) against the destination registers of instructions currently in the EX, MEM, and WB stages.

If a hazard is found:
- A bubble (NOP) is inserted into the pipeline
- The PC is held so the stalled instruction re-executes next cycle
- A stall counter is incremented


After running, the simulator prints the total cycles, number of stalls, and CPI (Cycles Per Instruction).

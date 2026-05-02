#!/usr/bin/env python3
"""
CS4200 - Assignment 6 (TEMPLATE - Cache Focus)
RV32I stage-based simulator (IF/ID/EX/MEM/WB) + Data Cache

IMPORTANT: Only the cache portion is TODO-masked.
All prior-course topics (decode/control/ALU/datapath) are provided.

Input:
- hex_inst.txt (one 32-bit instruction per line, 8 hex chars), PC starts at 0.

Required outputs:
- trace.log
- regs_final.log
- dmem_final.log
- cache.log
- cache_stats.log

Assignment 6 focus:
- Implement set-associative cache with LRU, write-back + write-allocate.
- Route ALL lw/sw through cache (no direct dmem access in MEM stage for lw/sw).
- Produce cache logs/stats proving correctness.
"""

MASK32 = 0xFFFFFFFF

# ------------------------------------------------------------
# Cache configuration (fixed for grading)
# ------------------------------------------------------------
CACHE_SIZE_BYTES = 256
BLOCK_BYTES = 16            # 4 words per block
ASSOC = 2                   # 2-way set associative

WORD_BYTES = 4
WORDS_PER_BLOCK = BLOCK_BYTES // WORD_BYTES
NUM_SETS = (CACHE_SIZE_BYTES // BLOCK_BYTES) // ASSOC


# ------------------------------------------------------------
# 32-bit helpers (given)
# ------------------------------------------------------------
def u32(x):
    return x & MASK32


def s32(x):
    x = x & MASK32
    if x & 0x80000000:
        return x - 0x100000000
    return x


def sign_extend(value, bits):
    mask = (1 << bits) - 1
    v = value & mask
    sign_bit = 1 << (bits - 1)
    if v & sign_bit:
        v = v - (1 << bits)
    return v


def get_bits(x, hi, lo):
    width = hi - lo + 1
    return (x >> lo) & ((1 << width) - 1)


# ------------------------------------------------------------
# Immediate generators (given)
# ------------------------------------------------------------
def imm_i(instr):
    return sign_extend(get_bits(instr, 31, 20), 12)


def imm_s(instr):
    hi = get_bits(instr, 31, 25)
    lo = get_bits(instr, 11, 7)
    return sign_extend((hi << 5) | lo, 12)


def imm_b(instr):
    b12 = get_bits(instr, 31, 31)
    b11 = get_bits(instr, 7, 7)
    b10_5 = get_bits(instr, 30, 25)
    b4_1 = get_bits(instr, 11, 8)
    val = (b12 << 12) | (b11 << 11) | (b10_5 << 5) | (b4_1 << 1)
    return sign_extend(val, 13)


def imm_u(instr):
    return get_bits(instr, 31, 12) << 12


def imm_j(instr):
    j20 = get_bits(instr, 31, 31)
    j10_1 = get_bits(instr, 30, 21)
    j11 = get_bits(instr, 20, 20)
    j19_12 = get_bits(instr, 19, 12)
    val = (j20 << 20) | (j19_12 << 12) | (j11 << 11) | (j10_1 << 1)
    return sign_extend(val, 21)


# ------------------------------------------------------------
# Decode + Control (given subset)
# ------------------------------------------------------------
def decode(instr):
    d = {}
    d["instr"] = u32(instr)
    d["opcode"] = get_bits(instr, 6, 0)
    d["rd"] = get_bits(instr, 11, 7)
    d["funct3"] = get_bits(instr, 14, 12)
    d["rs1"] = get_bits(instr, 19, 15)
    d["rs2"] = get_bits(instr, 24, 20)
    d["funct7"] = get_bits(instr, 31, 25)

    d["imm_I"] = imm_i(instr)
    d["imm_S"] = imm_s(instr)
    d["imm_B"] = imm_b(instr)
    d["imm_U"] = imm_u(instr)
    d["imm_J"] = imm_j(instr)
    return d


def main_control(d):
    op = d["opcode"]
    f3 = d["funct3"]

    c = {
        "RegWrite": 0,
        "MemRead": 0,
        "MemWrite": 0,
        "MemToReg": 0,
        "ALUSrc": 0,
        "Branch": 0,
        "Jump": 0,
        "JumpReg": 0,
        "ALUOp": "ADDR",
        "ImmSel": None,
        "BrType": None,
    }

    if op == 0x33:  # R
        c["RegWrite"] = 1
        c["ALUSrc"] = 0
        c["ALUOp"] = "R"

    elif op == 0x13:  # I-ALU
        c["RegWrite"] = 1
        c["ALUSrc"] = 1
        c["ALUOp"] = "I"
        c["ImmSel"] = "I"

    elif op == 0x03:  # lw
        c["RegWrite"] = 1
        c["MemRead"] = 1
        c["MemToReg"] = 1
        c["ALUSrc"] = 1
        c["ALUOp"] = "ADDR"
        c["ImmSel"] = "I"

    elif op == 0x23:  # sw
        c["MemWrite"] = 1
        c["ALUSrc"] = 1
        c["ALUOp"] = "ADDR"
        c["ImmSel"] = "S"

    elif op == 0x63:  # branches
        c["Branch"] = 1
        c["ALUSrc"] = 0
        c["ALUOp"] = "BR"
        c["ImmSel"] = "B"
        if f3 == 0b000:
            c["BrType"] = "beq"
        elif f3 == 0b001:
            c["BrType"] = "bne"
        elif f3 == 0b100:
            c["BrType"] = "blt"
        elif f3 == 0b101:
            c["BrType"] = "bge"
        elif f3 == 0b110:
            c["BrType"] = "bltu"
        elif f3 == 0b111:
            c["BrType"] = "bgeu"

    elif op == 0x6F:  # jal
        c["Jump"] = 1
        c["RegWrite"] = 1
        c["ALUSrc"] = 1
        c["ALUOp"] = "ADDR"
        c["ImmSel"] = "J"

    elif op == 0x67:  # jalr
        c["Jump"] = 1
        c["JumpReg"] = 1
        c["RegWrite"] = 1
        c["ALUSrc"] = 1
        c["ALUOp"] = "ADDR"
        c["ImmSel"] = "I"

    return c


def select_imm(d, c):
    sel = c["ImmSel"]
    if sel == "I":
        return d["imm_I"]
    if sel == "S":
        return d["imm_S"]
    if sel == "B":
        return d["imm_B"]
    if sel == "U":
        return d["imm_U"]
    if sel == "J":
        return d["imm_J"]
    return 0


def alu_control(c, d):
    op = c["ALUOp"]
    f3 = d["funct3"]
    f7 = d["funct7"]

    if op == "ADDR":
        return "ADD"
    if op == "BR":
        return "SUB"

    if op == "R":
        if f3 == 0b000:
            return "SUB" if f7 == 0b0100000 else "ADD"
        if f3 == 0b111:
            return "AND"
        if f3 == 0b110:
            return "OR"
        if f3 == 0b100:
            return "XOR"
        if f3 == 0b001:
            return "SLL"
        if f3 == 0b101:
            return "SRA" if f7 == 0b0100000 else "SRL"
        if f3 == 0b010:
            return "SLT"
        if f3 == 0b011:
            return "SLTU"
        return "ADD"

    if op == "I":
        if f3 == 0b000:
            return "ADD"
        if f3 == 0b111:
            return "AND"
        if f3 == 0b110:
            return "OR"
        if f3 == 0b100:
            return "XOR"
        if f3 == 0b010:
            return "SLT"
        if f3 == 0b011:
            return "SLTU"
        if f3 == 0b001:
            return "SLL"
        if f3 == 0b101:
            return "SRA" if f7 == 0b0100000 else "SRL"
        return "ADD"

    return "ADD"


def alu_exec(alu_op, a, b):
    a = u32(a)
    b = u32(b)
    shamt = b & 0x1F

    if alu_op == "ADD":
        return u32(a + b)
    if alu_op == "SUB":
        return u32(a - b)
    if alu_op == "AND":
        return u32(a & b)
    if alu_op == "OR":
        return u32(a | b)
    if alu_op == "XOR":
        return u32(a ^ b)
    if alu_op == "SLL":
        return u32(a << shamt)
    if alu_op == "SRL":
        return u32(a >> shamt)
    if alu_op == "SRA":
        return u32(s32(a) >> shamt)
    if alu_op == "SLT":
        return 1 if s32(a) < s32(b) else 0
    if alu_op == "SLTU":
        return 1 if u32(a) < u32(b) else 0

    return u32(a + b)


def branch_taken(br_type, rs1_val, rs2_val):
    if br_type == "beq":
        return u32(rs1_val) == u32(rs2_val)
    if br_type == "bne":
        return u32(rs1_val) != u32(rs2_val)
    if br_type == "blt":
        return s32(rs1_val) < s32(rs2_val)
    if br_type == "bge":
        return s32(rs1_val) >= s32(rs2_val)
    if br_type == "bltu":
        return u32(rs1_val) < u32(rs2_val)
    if br_type == "bgeu":
        return u32(rs1_val) >= u32(rs2_val)
    return False


# ------------------------------------------------------------
# Stage functions (given)
# ------------------------------------------------------------
def stage_if(pc, imem):
    out = {}
    out["pc"] = u32(pc)
    out["pc_plus4"] = u32(pc + 4)
    out["instr"] = imem.get(u32(pc), None)
    return out


def stage_id(instr, regs, stall):
    d = decode(instr)
    c = main_control(d)
    imm = select_imm(d, c)

    out = {}
    out["d"] = d
    out["c"] = c.copy()
    out["imm"] = imm
    out["rs1"] = d["rs1"]
    out["rs2"] = d["rs2"]
    out["rd"] = d["rd"]
    out["rs1_val"] = u32(regs[d["rs1"]])
    out["rs2_val"] = u32(regs[d["rs2"]])

    #new code for project 
    #Takes the destination register (rd) from the decoded instruction fields (d)
    #and stores it in the output dictionary (out).
    out["rd"] = d["rd"]

    #Takes the RegWrite control signal from the main control unit (c)
    #and stores it in the output dictionary (out) to indicate whether the instruction
    #will write to a register in the write back stage.
    out["RegWrite"] = c["RegWrite"]

    if stall:
        # If a stall is required, disable register write and memory access
        out["c"]["RegWrite"] = 0
        out["c"]["MemRead"] = 0
        out["c"]["MemWrite"] = 0
    else:
        # If no stall is required, use the original control signals
        out["c"] = c

    return out

    


def stage_ex(pc, pc_plus4, id_out):
    d = id_out["d"]
    c = id_out["c"]
    imm = id_out["imm"]

    rs1_val = id_out["rs1_val"]
    rs2_val = id_out["rs2_val"]

    alu_op = alu_control(c, d)
    alu_in2 = imm if c["ALUSrc"] else rs2_val
    alu_res = alu_exec(alu_op, rs1_val, alu_in2)

    next_pc = u32(pc_plus4)
    taken = False

    if c["Branch"] and c["BrType"] is not None:
        taken = branch_taken(c["BrType"], rs1_val, rs2_val)
        if taken:
            next_pc = u32(pc + imm)

    if c["Jump"]:
        taken = True
        if c["JumpReg"]:
            next_pc = u32((rs1_val + imm) & 0xFFFFFFFE)
        else:
            next_pc = u32(pc + imm)

    out = {}
    out["alu_op"] = alu_op
    out["alu_res"] = u32(alu_res)
    out["next_pc"] = u32(next_pc)
    out["taken"] = taken
    out["pc_plus4"] = u32(pc_plus4)
    out["rs2_val"] = u32(rs2_val)   # used for sw
    out["RegWrite"] = c["RegWrite"]
    out["rd"] = id_out["rd"]
    return out


# ------------------------------------------------------------
# Backing memory helpers (given; DO NOT bypass cache for lw/sw)
# ------------------------------------------------------------
def mem_load_word(dmem, addr):
    """
    Given: backing memory read (word-aligned).
    IMPORTANT: In Assignment 6, your lw must call cache_access_lw (not this directly).
    This should only be used inside cache fill/writeback code.
    """
    if addr % 4 != 0:
        raise ValueError("Unaligned lw at address 0x%08X" % addr)
    return u32(dmem.get(addr, 0))


def mem_store_word(dmem, addr, value):
    """
    Given: backing memory write (word-aligned).
    IMPORTANT: In Assignment 6, your sw must call cache_access_sw (not this directly).
    This should only be used inside cache writeback code or cache flush.
    """
    if addr % 4 != 0:
        raise ValueError("Unaligned sw at address 0x%08X" % addr)
    dmem[addr] = u32(value)


# =====================================================================
# ==================== ASSIGNMENT 6: CACHE (TODO) =====================
# =====================================================================

def cache_make():
    """
    TODO (A6 CORE):
    Create and return the cache data structure.

    Recommended structure:
      cache = list of NUM_SETS sets
      each set = list of ASSOC lines (ways)
      each line is a dict with:
        valid : 0/1
        dirty : 0/1
        tag   : int
        data  : list of WORDS_PER_BLOCK words (each 32-bit)
        lru   : integer counter used for LRU replacement

    Notes:
    - Initialize all lines invalid and not dirty.
    - data array should be filled with zeros initially.
    """
    cache = []
    for _ in range(NUM_SETS):        
        loop_set = []
        for _ in range(ASSOC):       
            loop_set.append({
                "valid": 0,
                "dirty": 0,
                "tag":   0,
                "data":  [0] * WORDS_PER_BLOCK,  
                "lru":   0,
            })
        cache.append(loop_set)
    return cache








def cache_addr_parts(addr):
    """
    TODO (A6 CORE):
    Given a BYTE address, compute (tag, set_index, word_offset) for this cache.

    You must follow this standard mapping:
      offset_bytes = addr % BLOCK_BYTES
      block_addr   = addr // BLOCK_BYTES
      set_index    = block_addr % NUM_SETS
      tag          = block_addr // NUM_SETS
      word_offset  = offset_bytes // 4    (0..WORDS_PER_BLOCK-1)

    Return: (tag, set_index, word_offset)
    """

    offset_bytes = addr % BLOCK_BYTES
    block_addr   = addr // BLOCK_BYTES
    set_index    = block_addr % NUM_SETS
    tag          = block_addr // NUM_SETS
    word_offset  = offset_bytes // 4 

    return tag, set_index, word_offset


def cache_block_base_addr(tag, set_index):
    """
    TODO (A6 CORE):
    Compute the byte base address of the block identified by (tag, set_index).

    Reverse mapping:
      block_addr = tag * NUM_SETS + set_index
      base_addr  = block_addr * BLOCK_BYTES

    Return base_addr (byte address).
    """
    block_addr = tag * NUM_SETS + set_index
    base_addr  = block_addr * BLOCK_BYTES
    return base_addr


def cache_touch_lru(cache, set_index, used_way):
    """
    TODO (A6 CORE):
    Update LRU metadata so that (set_index, used_way) is MOST recently used.

    Simple approach:
      - let used_way.lru = max_lru_in_set + 1

    Any consistent LRU implementation is okay, as long as:
      - victim selection chooses least-recently-used line among valid lines
      - accesses update recency
    """
    current_set = cache[set_index]

    max_lru = max(line["lru"] for line in current_set)

    current_set[used_way]["lru"] = max_lru + 1


def cache_choose_victim(cache, set_index):
    """
    TODO (A6 CORE):
    Choose a victim way in the given set:
      - If any invalid line exists, return that way first.
      - Else return way with smallest lru value (least recently used).

    Return: victim_way (0..ASSOC-1)
    """
    set = cache[set_index]

    for way, line in enumerate(set):
        if line["valid"] == 0:
            return way
    lru_way = min(range(ASSOC), key=lambda x: set[x]["lru"])

    return lru_way


def cache_writeback_if_needed(dmem, cache, set_index, way, cache_lines_log, stats):
    """
    TODO (A6 CORE):
    If the chosen line is valid AND dirty:
      - Write back the entire block to backing memory (dmem) word-by-word.
      - Record a log line into cache_lines_log such as:
          "WB | set=... way=... tag=... base=..."
      - Increment stats["writebacks"]
      - Clear dirty bit.

    Use:
      base_addr = cache_block_base_addr(old_tag, set_index)
      For each word i in block:
        mem_store_word(dmem, base_addr + i*4, line.data[i])
    """
    new_set = cache[set_index]
    line = new_set[way]
    is_valid = line["valid"] == 1
    is_dirty = line["dirty"] == 1
    if is_valid and is_dirty:
        old = line["tag"]
        base_addr = cache_block_base_addr(old, set_index)
        for i in range(WORDS_PER_BLOCK):
            word = line["data"][i]
            word_addr = base_addr + i * 4
            mem_store_word(dmem, word_addr, word)
        cache_lines_log.append("WB | set=%d way=%d tag=0x%X base=0x%08X" % (set_index, way, old, base_addr))
        stats["writebacks"] += 1
        line["dirty"] = 0


def cache_fill_block_from_mem(dmem, cache, set_index, way, tag):
    """
    TODO (A6 CORE):
    Fill a cache line from backing memory (read entire block):
      - base_addr = cache_block_base_addr(tag, set_index)
      - for each i in 0..WORDS_PER_BLOCK-1:
          line.data[i] = mem_load_word(dmem, base_addr + i*4)
      - set valid=1, dirty=0, tag=tag
    """
    line = cache[set_index][way]

    base_addr = cache_block_base_addr(tag, set_index)

    for i in range(WORDS_PER_BLOCK):
        line["data"][i] = mem_load_word(dmem, base_addr + i * 4)

    line["valid"] = 1
    line["dirty"] = 0
    line["tag"] = tag


def cache_access_lw(dmem, cache, addr, cache_lines_log, stats):
    """
    TODO (A6 CORE): Cache read access for lw.

    Requirements:
    - Enforce word alignment (addr % 4 == 0). If not aligned, raise ValueError.
    - Compute (tag, set_index, word_offset).
    - HIT:
        - stats["lw_hits"] += 1
        - update LRU
        - log: "LW HIT | addr=... set=... way=... tag=... woff=... val=..."
        - return the word from cache line.data[word_offset]
    - MISS (write-allocate):
        - stats["lw_misses"] += 1
        - choose victim (invalid first else LRU)
        - if victim valid: log eviction line (include dirty)
        - if victim dirty: write back (cache_writeback_if_needed)
        - fill victim from memory (cache_fill_block_from_mem)
        - update LRU
        - log: "LW MISS | addr=... set=... way=... tag=... woff=... val=..."
        - return loaded word
    """
    if addr % 4 != 0:
        raise ValueError("Unaligned lw at address 0x%08X" % addr)
    
    tag, set_index, word_offset = cache_addr_parts(addr)
    current_set = cache[set_index]

    for way in range(ASSOC):
        line = current_set[way]
        if line["valid"] == 1 and line["tag"] == tag:
            stats["lw_hits"] += 1
            cache_touch_lru(cache, set_index, way)
            val = line["data"][word_offset]
            hit_log = "LW HIT  | addr=0x%08X set=%d way=%d tag=0x%X woff=%d val=0x%08X" % (addr, set_index, way, tag, word_offset, val)
            cache_lines_log.append(hit_log)
            return val
    
    stats["lw_misses"] += 1
    victim_way = cache_choose_victim(cache, set_index)
    victim_line = current_set[victim_way]

    if victim_line["valid"] == 1:
        evict_log = "EVICT | set=%d way=%d tag=0x%X dirty=%d" % (set_index, victim_way, victim_line["tag"], victim_line["dirty"])
        cache_lines_log.append(evict_log)
        cache_writeback_if_needed(dmem, cache, set_index, victim_way, cache_lines_log, stats)
    
    cache_fill_block_from_mem(dmem, cache, set_index, victim_way, tag)
    cache_touch_lru(cache, set_index, victim_way)
    filled_line = current_set[victim_way]
    val = filled_line["data"][word_offset]
    miss_log = "LW MISS | addr=0x%08X set=%d way=%d tag=0x%X woff=%d val=0x%08X" % (addr, set_index, victim_way, tag, word_offset, val)
    cache_lines_log.append(miss_log)

    return val


def cache_access_sw(dmem, cache, addr, value, cache_lines_log, stats):
    """
    TODO (A6 CORE): Cache write access for sw.

    Requirements:
    - Enforce word alignment. If not aligned, raise ValueError.
    - Write-back + write-allocate policy.

    HIT:
      - stats["sw_hits"] += 1
      - update line.data[word_offset] = value
      - mark dirty=1
      - update LRU
      - log: "SW HIT | addr=... set=... way=... tag=... woff=... val=..."

    MISS (write-allocate):
      - stats["sw_misses"] += 1
      - choose victim (invalid first else LRU)
      - if victim valid: log eviction (dirty?)
      - if victim dirty: write back block
      - fill block from memory
      - perform store to cache line + set dirty=1
      - update LRU
      - log: "SW MISS | addr=... set=... way=... tag=... woff=... val=..."
    """
    if addr % 4 != 0:
        raise ValueError("Unaligned sw at address 0x%08X" % addr)

    tag, set_index, word_offset = cache_addr_parts(addr)
    the_set = cache[set_index]

    for way in range(ASSOC):
        this_line = the_set[way]
        line_is_valid = this_line["valid"] == 1
        line_matches = this_line["tag"] == tag
        if line_is_valid and line_matches:
            stats["sw_hits"] += 1
            this_line["data"][word_offset] = value
            this_line["dirty"] = 1
            cache_touch_lru(cache, set_index, way)
            hit_message = "SW HIT  | addr=0x%08X set=%d way=%d tag=0x%X woff=%d val=0x%08X" % (addr, set_index, way, tag, word_offset, value)
            cache_lines_log.append(hit_message)
            return

    stats["sw_misses"] += 1
    victim_way = cache_choose_victim(cache, set_index)
    victim_line = the_set[victim_way]
    old_line_is_valid = victim_line["valid"] == 1

    if old_line_is_valid:
        evict_message = "EVICT | set=%d way=%d tag=0x%X dirty=%d" % (set_index, victim_way, victim_line["tag"], victim_line["dirty"])
        cache_lines_log.append(evict_message)
        cache_writeback_if_needed(dmem, cache, set_index, victim_way, cache_lines_log, stats)

    cache_fill_block_from_mem(dmem, cache, set_index, victim_way, tag)
    new_line = the_set[victim_way]
    new_line["data"][word_offset] = value
    new_line["dirty"] = 1
    cache_touch_lru(cache, set_index, victim_way)
    miss_message = "SW MISS | addr=0x%08X set=%d way=%d tag=0x%X woff=%d val=0x%08X" % (addr, set_index, victim_way, tag, word_offset, value)
    cache_lines_log.append(miss_message)


def cache_flush_all(dmem, cache, cache_lines_log, stats):
    """
    TODO (A6 CORE):
    At program end, flush the cache:
      - for every set and every way:
          write back if dirty (cache_writeback_if_needed)
      - log: "CACHE FLUSH DONE"
    """
    for index in range(NUM_SETS):
        for way in range(ASSOC):
            cache_writeback_if_needed(dmem, cache, index, way, cache_lines_log, stats)
    cache_lines_log.append("CACHE FLUSH DONE")


def stage_mem_with_cache(id_out, ex_out, cache, dmem, cache_lines_log, stats):
    """
    TODO (A6 CORE):
    This replaces the normal MEM stage for Assignment 6.

    Requirements:
    - Determine if instruction is lw or sw using control signals:
        if c["MemRead"] -> lw
        if c["MemWrite"] -> sw
      (funct3==010 for word)
    - For lw:
        mem_data = cache_access_lw(dmem, cache, addr, cache_lines_log, stats)
        stats["lw_total"] += 1
    - For sw:
        cache_access_sw(dmem, cache, addr, store_val, cache_lines_log, stats)
        stats["sw_total"] += 1
    - Must return dict:
        out["mem_data"]
        out["addr"]
        out["cache_event"] = "LW" or "SW" or "" (used by trace.log)

    IMPORTANT:
    - Do NOT call mem_load_word/mem_store_word here for lw/sw.
      Only cache functions may touch backing memory.
    """
    out = {}
    out["mem_data"] = 0
    out["addr"] = 0
    out["cache_event"] = ""
    out["RegWrite"] = id_out["c"]["RegWrite"]
    out["rd"] = id_out["rd"]

    control = id_out["c"]
    decode = id_out["d"]
    addr = ex_out["alu_res"]
    funct3 = decode["funct3"]

    if control["MemRead"] and funct3 == 0b010:
        loaded_word = cache_access_lw(dmem, cache, addr, cache_lines_log, stats)
        stats["lw_total"] += 1
        out["mem_data"] = loaded_word
        out["addr"] = addr
        out["cache_event"] = "LW"

    elif control["MemWrite"] and funct3 == 0b010:
        store_val = ex_out["rs2_val"]
        cache_access_sw(dmem, cache, addr, store_val, cache_lines_log, stats)
        stats["sw_total"] += 1
        out["addr"] = addr
        out["cache_event"] = "SW"

    return out

def write_cache_stats(path, stats):
    """
    TODO (A6 CORE):
    Write cache_stats.log with:
      - config line (size, block, assoc, sets)
      - total accesses (lw_total + sw_total), and breakdown
      - hits and misses (lw_hits/lw_misses/sw_hits/sw_misses)
      - hit rate = hits / total_accesses (handle divide by 0)
      - writebacks count

    Use simple plain text lines; one item per line is fine.
    """
    total_accesses = stats["lw_total"] + stats["sw_total"]
    total_hits = stats["lw_hits"] + stats["sw_hits"]
    total_misses = stats["lw_misses"] + stats["sw_misses"]

    if total_accesses == 0:
        hit_rate = 0.0
    else:
        hit_rate = total_hits / total_accesses

    the_file = open(path, "w", encoding="utf-8")


    the_file.write("Cache config: size=%dB  block=%dB  assoc=%d  sets=%d\n" % (CACHE_SIZE_BYTES, BLOCK_BYTES, ASSOC, NUM_SETS))
    the_file.write("---------------------------------------------\n")
    the_file.write("Total accesses : %d\n" % total_accesses)
    the_file.write("  Loads        : %d\n" % stats["lw_total"])
    the_file.write("  Stores       : %d\n" % stats["sw_total"])
    the_file.write("---------------------------------------------\n")
    the_file.write("Hits           : %d\n" % total_hits)
    the_file.write("Misses         : %d\n" % total_misses)
    the_file.write("Hit rate       : %.2f\n" % hit_rate)
    the_file.write("---------------------------------------------\n")
    the_file.write("Writebacks     : %d\n" % stats["writebacks"])
    the_file.close()


# =====================================================================
# =================== END OF ASSIGNMENT 6 CACHE TODO ==================
# =====================================================================


# ------------------------------------------------------------
# WB stage (given)
# ------------------------------------------------------------
def stage_wb(pc_plus4, id_out, ex_out, mem_out, regs):
    c = id_out["c"]
    rd = id_out["rd"]

    wb_val = ex_out["alu_res"]
    if c["MemToReg"]:
        wb_val = mem_out["mem_data"]

    if c["Jump"] and c["RegWrite"]:
        wb_val = u32(pc_plus4)

    did_write = False
    if c["RegWrite"] and rd != 0:
        regs[rd] = u32(wb_val)
        did_write = True

    regs[0] = 0

    out = {}
    out["wb_val"] = u32(wb_val)
    out["wb_rd"] = rd
    out["did_write"] = did_write
    out["rd"] = rd
    out["RegWrite"] = 1 if (c["RegWrite"] and rd != 0) else 0
    return out


# ------------------------------------------------------------
# Trace helpers (given)
# ------------------------------------------------------------
def try_mnemonic(d):
    op = d["opcode"]
    f3 = d["funct3"]
    f7 = d["funct7"]

    if op == 0x33:
        if f3 == 0b000:
            return "sub" if f7 == 0b0100000 else "add"
        if f3 == 0b111:
            return "and"
        if f3 == 0b110:
            return "or"
        if f3 == 0b100:
            return "xor"
        if f3 == 0b001:
            return "sll"
        if f3 == 0b101:
            return "sra" if f7 == 0b0100000 else "srl"
        if f3 == 0b010:
            return "slt"
        if f3 == 0b011:
            return "sltu"
        return "r?"

    if op == 0x13:
        if f3 == 0b000:
            return "addi"
        if f3 == 0b111:
            return "andi"
        if f3 == 0b110:
            return "ori"
        if f3 == 0b100:
            return "xori"
        if f3 == 0b010:
            return "slti"
        if f3 == 0b011:
            return "sltiu"
        if f3 == 0b001:
            return "slli"
        if f3 == 0b101:
            return "srai" if f7 == 0b0100000 else "srli"
        return "i?"

    if op == 0x03 and f3 == 0b010:
        return "lw"
    if op == 0x23 and f3 == 0b010:
        return "sw"
    if op == 0x63:
        return {
            0b000: "beq",
            0b001: "bne",
            0b100: "blt",
            0b101: "bge",
            0b110: "bltu",
            0b111: "bgeu",
        }.get(f3, "b?")
    if op == 0x6F:
        return "jal"
    if op == 0x67:
        return "jalr"

    return "?"


def trace_line(step, if_out, id_out, ex_out, mem_out, wb_out):
    d = id_out["d"]
    c = id_out["c"]
    mnem = try_mnemonic(d)

    parts = []
    parts.append("step=%d" % step)
    parts.append("pc=0x%08X" % if_out["pc"])
    parts.append("instr=0x%08X" % d["instr"])
    parts.append("mn=%s" % mnem)

    parts.append("RegW=%d MemR=%d MemW=%d M2R=%d ALUSrc=%d Br=%d J=%d" % (
        c["RegWrite"], c["MemRead"], c["MemWrite"], c["MemToReg"], c["ALUSrc"], c["Branch"], c["Jump"]
    ))

    parts.append("alu=%s res=0x%08X" % (ex_out["alu_op"], ex_out["alu_res"]))

    if c["MemRead"] or c["MemWrite"]:
        parts.append("mem@0x%08X rdata=0x%08X" % (mem_out["addr"], mem_out["mem_data"]))
        if mem_out.get("cache_event", ""):
            parts.append("cache=%s" % mem_out["cache_event"])

    if wb_out["did_write"]:
        parts.append("wb=x%d<-0x%08X" % (wb_out["wb_rd"], wb_out["wb_val"]))

    parts.append("next_pc=0x%08X" % ex_out["next_pc"])
    return " | ".join(parts)


# ------------------------------------------------------------
# Loader + log writers (given)
# ------------------------------------------------------------
def load_imem_from_file(path):
    imem = {}
    pc = 0
    f = open(path, "r", encoding="utf-8")
    for line in f:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.lower().startswith("0x"):
            s = s[2:]
        instr = int(s, 16) & MASK32
        imem[pc] = instr
        pc += 4
    f.close()
    return imem


def write_lines(path, lines):
    f = open(path, "w", encoding="utf-8")
    i = 0
    while i < len(lines):
        f.write(lines[i] + "\n")
        i += 1
    f.close()


def write_regs_log(regs, path):
    f = open(path, "w", encoding="utf-8")
    for i in range(32):
        f.write("x%-2d = 0x%08X (%d)\n" % (i, u32(regs[i]), s32(regs[i])))
    f.close()


def write_dmem_log(dmem, path):
    f = open(path, "w", encoding="utf-8")
    for a in sorted(dmem.keys()):
        f.write("0x%08X : 0x%08X (%d)\n" % (u32(a), u32(dmem[a]), s32(dmem[a])))
    f.close()


#compares the instruction in the ID stage with the instructions in 
#ex, mem, and write bak stages to determine if the instruction in
#id wants to read from a register that oneof those instructions is writting
#to but has not yet written to.
def hazard_detection(id_output, ex_output, mem_output, wb_output):

    
    #checks for read after write errors by 
    #seeing if the ex_output instruction will
    #perform a register write operation and  
    #if that operation's destination register is not rd 0 which has a constant value of 0,
    #and if the destination register of the ex stage instruction is the same register of 
    #either of the source registers of the id stage instruction.
    if (
        ex_output["RegWrite"] and ex_output["rd"] != 0 and (ex_output["rd"] == id_output["rs1"] 
        or ex_output["rd"] == id_output["rs2"])
    ):
        return True
    
    # Check for RAW hazards between ID and MEM stages
    # by checking if the MEM instruction will perform a register write operation,
    # if the destination register of the MEM instruction is not zero,
    # and if the destination register of the MEM instruction matches either
    # of the source registers of the ID instruction.
    if (
        mem_output["RegWrite"] and mem_output["rd"] != 0 and (mem_output["rd"] == id_output["rs1"]
        or mem_output["rd"] == id_output["rs2"])
    ):
        return True

    #Check for RAW hazards between ID and WB stages
    #by checking if the WB instruction will perform a register write operation,
    #if the destination register of the WB instruction is not zero,
    #and if the destination register of the WB instruction matches either
    #of the source registers of the ID instruction.
    if (
        wb_output["RegWrite"] and wb_output["rd"] != 0 and (wb_output["rd"] == id_output["rs1"]
        or wb_output["rd"] == id_output["rs2"])
    ):
        return True
    
    return False 
    
# ------------------------------------------------------------
# Main (given skeleton, cache TODOs plugged in)
# ------------------------------------------------------------
def main():

    #initalizes the num stalls variable to track how many stall cycles happened
    num_stalls = 0

    #initalizes placeholder values for the three stages 
    ex_out = {"RegWrite": 0, "rd": 0}
    mem_out = {"RegWrite": 0, "rd": 0}
    wb_out = {"RegWrite": 0, "rd": 0}

    imem = load_imem_from_file("hex_inst.txt")

    regs = [0] * 32
    dmem = {}  # backing memory (word-addressed dict)

    # TODO (A6): create cache structure
    cache = cache_make()

    cache_lines_log = []
    trace_lines = []

    # Stats dictionary (you must update these in cache + MEM stage)
    stats = {
        "lw_total": 0,
        "sw_total": 0,
        "lw_hits": 0,
        "lw_misses": 0,
        "sw_hits": 0,
        "sw_misses": 0,
        "writebacks": 0,
    }


   
    pc = 0
    steps = 0
    max_steps = 10_000_000
    id_out = {"rs1": 0, "rs2": 0}



    while steps < max_steps:
        if_out = stage_if(pc, imem)
        if if_out["instr"] is None:
            break

        

        pc_plus4 = if_out["pc_plus4"]
        instr = if_out["instr"]

        
        

        hazard_bool = hazard_detection(id_out, ex_out, mem_out, wb_out)

        id_out = stage_id(instr, regs, hazard_bool)
        
        if hazard_bool:
            # If a hazard is detected, insert a bubble in the EX stage
            ex_out = {"RegWrite": 0, "rd": 0, "next_pc": pc_plus4, "alu_res": 0, "rs2_val": 0, "alu_op": "ADD", "taken": False, "pc_plus4": pc_plus4}
            num_stalls += 1
        else:
            ex_out = stage_ex(if_out["pc"], pc_plus4, id_out)

        # TODO (A6): MEM stage must use cache for lw/sw
        mem_out = stage_mem_with_cache(id_out, ex_out, cache, dmem, cache_lines_log, stats)

        wb_out = stage_wb(pc_plus4, id_out, ex_out, mem_out, regs)

        trace_lines.append(trace_line(steps, if_out, id_out, ex_out, mem_out, wb_out))

        pc = u32(ex_out["next_pc"])
        regs[0] = 0
        steps += 1

    # TODO (A6): flush dirty cache lines back to memory at end
    cache_flush_all(dmem, cache, cache_lines_log, stats)


    # write logs
    write_lines("trace.log", trace_lines)
    write_regs_log(regs, "regs_final.log")
    write_dmem_log(dmem, "dmem_final.log")
    write_lines("cache.log", cache_lines_log)

    # TODO (A6): create cache_stats.log
    write_cache_stats("cache_stats.log", stats)

    print("HALT")
    print("steps =", steps)
    print("final pc = 0x%08X" % u32(pc))
    print("wrote trace.log, regs_final.log, dmem_final.log, cache.log, cache_stats.log")

    print(f"Number of stalls: {num_stalls}")
    print(f"CPI: {(steps + num_stalls) / steps:.2f}")


if __name__ == "__main__":
    main()

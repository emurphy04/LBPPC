"""List the stores in LittleBigPlanet 3's code that run below the red zone on Windows: patches/redzone.json.

Windows writes an exception's frame right below rsp, over the System V red zone where leaf functions keep live
locals (the PS4 and Linux leave those 128 bytes alone); where it saves extended state in the compact XSAVE layout
(Intel 12th gen and later) that frame holds the AVX registers up to 0x28 bytes below rsp. A store that faults on a
page the GPU tracker write-protected is resumed with those locals overwritten. In LBP3 that crashed co-op in
spu.prx (0x16de8e7: a loop's index reloaded from [rsp-0x20] after its store faulted, then a misaligned vmovaps).
link_modules.protect_red_zones runs each site's stores with rsp 128 bytes lower.

Unlike Bloodborne, LBP3 runs code from the modules linked with its eboot (libc, Fios2, the FMOD plugins and
spu.prx), so the image scanned is the linked one (link_modules.link, before its red-zone step), each module's
functions found from its own .eh_frame_hdr at its linked address. Sites within 32 bytes of LBPPC's own code changes
(exact_math_sites.inc, the patch sites probe.c names) are left out: those check the original bytes and would
otherwise refuse to apply.

A site is a run of a red-zone leaf's instructions, at least 5 bytes for a jmp, holding its stores through non-stack
pointers; nothing inside it may be a branch target, rip-relative, segment-prefixed or change rsp. Functions with
jump tables are left out.

Developer tool (pip install capstone): python tools/redzone_sites.py <game dir> [--update <update dir>]
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile

import capstone
from capstone import x86

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import game_view  # noqa: E402
import link_modules  # noqa: E402
from prepare import parse_self  # noqa: E402

RSP, RBP, RIP = x86.X86_REG_RSP, x86.X86_REG_RBP, x86.X86_REG_RIP
STACK = {RSP, x86.X86_REG_ESP, x86.X86_REG_SP, x86.X86_REG_SPL}
FLOW = {capstone.CS_GRP_JUMP, capstone.CS_GRP_CALL, capstone.CS_GRP_RET, capstone.CS_GRP_INT, capstone.CS_GRP_IRET}
NO_ACCESS = ('lea', 'nop', 'prefetch')
RED_ZONE_RSP = re.compile(rb'[\x44\x4c\x54\x5c\x64\x6c\x74\x7c]\x24[\x80-\xff]', re.S)  # [rsp-disp8]
FRAME_POINTER = bytes.fromhex('4889e5')  # mov rbp, rsp
KEEP_CLEAR = 32  # bytes around LBPPC's own code changes


def linked(game, modules):
    """(the linked image before cpu_compat and the red zone, its module table) as link_modules builds it."""
    captured = {}
    real = link_modules.protect_image

    def capture(image, segments, untranslated, relocs, path=None):
        captured['image'] = bytes(untranslated)
        return dict(enabled=False, sites=0, available=0, sites_sha256=None, cpu_overlaps=[], trampoline_bytes=0,
                    segment=None)
    link_modules.protect_image = capture
    try:
        with tempfile.TemporaryDirectory() as out:
            # prepare.py's output first (boot.bin, eboot.elf), as run.py runs it
            import subprocess
            args = [sys.executable, str(ROOT / 'scripts' / 'prepare.py'), str(game.base), '--out', out]
            if game.update:
                args += ['--update', str(game.update)]
            subprocess.run(args, check=True, stdout=subprocess.DEVNULL)
            link_modules.link(game, Path(out), modules)
            table = json.loads((Path(out) / 'link.json').read_text(encoding='utf-8'))['modules']
    finally:
        link_modules.protect_image = real
    return captured['image'], table


def function_starts(image, elf_bytes, base):
    """Function starts in one object's text (from its .eh_frame_hdr), at its linked address, and its text end."""
    _, _, ph, _, _ = parse_self(elf_bytes)
    loads = [p for p in ph if p['type'] == 1]
    text = next(p for p in loads if p['flags'] & 1)
    hdr = base + next(p for p in ph if p['type'] == 0x6474e550)['vaddr']
    if image[hdr + 2] != 0x03 or image[hdr + 3] != 0x3b:
        raise ValueError(f'unexpected .eh_frame_hdr encoding at {hdr:#x}')
    count, = struct.unpack_from('<I', image, hdr + 8)
    starts = sorted({hdr + struct.unpack_from('<i', image, hdr + 12 + 8 * i)[0] for i in range(count)})
    start, end = base + text['vaddr'], base + text['vaddr'] + text['filesz']
    return [s for s in starts if start <= s < end] + [end]


def lbppc_changes():
    """Address ranges LBPPC changes in memory: the exact-maths sites and the patch sites probe.c names."""
    ranges = []
    for line in (ROOT / 'src' / 'exact_math_sites.inc').read_text(encoding='ascii').splitlines():
        m = re.match(r'\{(0x[0-9a-f]+),(\d+),(\d+),', line)
        if m:
            offset = int(m.group(1), 16)
            ranges.append((offset, offset + max(int(m.group(2)), int(m.group(3)))))
    probe = (ROOT / 'src' / 'probe.c').read_text(encoding='utf-8', errors='replace')
    for m in re.finditer(r'\b[A-Z0-9_]*SITE[A-Z0-9_]*\s*=\s*(0x[0-9a-fA-F]+)', probe):
        offset = int(m.group(1), 16)
        ranges.append((offset, offset + 16))
    return sorted(ranges)


def stack_layout(insns):
    """rsp after the prologue and rbp as a frame pointer, both relative to the entry rsp."""
    rsp, rbp = 0, None
    for i in insns:
        ops = i.operands
        if i.mnemonic == 'push':
            rsp -= 8
        elif i.mnemonic == 'sub' and ops[0].type == x86.X86_OP_REG and ops[0].reg == RSP and ops[1].type == x86.X86_OP_IMM:
            rsp -= ops[1].imm
        elif i.mnemonic == 'mov' and ops[0].type == x86.X86_OP_REG and ops[0].reg == RBP and \
                ops[1].type == x86.X86_OP_REG and ops[1].reg == RSP:
            rbp = rsp
        else:
            break
    return rsp, rbp


READ_ONLY_FIRST = ('cmp', 'test', 'bt', 'ptest', 'vptest', 'ucomis', 'comis', 'vucomis', 'vcomis', 'prefetch',
                   'clflush', 'push')


def writes(i, op, n):
    """Does `i` write its memory operand `op` (operand number n)? capstone 5 gets AVX stores wrong: the memory
    operand of vmovaps [rbx+rax*4], xmm1 is given as read (movaps's correctly as written). In Intel syntax a
    memory first operand of an instruction with two or more operands is its destination, except for those that
    only compare or test it."""
    if op.access & capstone.CS_AC_WRITE:
        return True
    return n == 0 and len(i.operands) >= 2 and not i.mnemonic.startswith(READ_ONLY_FIRST)


def movable(i):
    _, written = i.regs_access()
    return not FLOW & set(i.groups) and not STACK & set(written) and i.prefix[1] not in (0x64, 0x65) and \
        not any(op.type == x86.X86_OP_MEM and op.mem.base == RIP for op in i.operands)


def analyse(md, image, start, end):
    """(sites, uncovered stores) of one function; None when it keeps nothing below rsp."""
    insns = []
    for i in md.disasm(image[start:end], start):
        if i.group(capstone.CS_GRP_CALL):
            return None  # not a leaf: no red zone
        insns.append(i)
    if not insns:
        return None
    body_rsp, rbp = stack_layout(insns)
    targets, red_zone, stores = set(), False, []
    for i in insns:
        if i.group(capstone.CS_GRP_JUMP):
            op = i.operands[0]
            if op.type == x86.X86_OP_IMM:
                targets.add(op.imm)
            elif not (op.type == x86.X86_OP_MEM and op.mem.base == RIP):
                return [], ['jump table']  # targets unknown: left alone
        for n, op in enumerate(i.operands):
            if op.type != x86.X86_OP_MEM:
                continue
            if op.mem.base == RSP and op.mem.disp < 0:
                red_zone = True
            if rbp is not None and op.mem.base == RBP and rbp + op.mem.disp < body_rsp:
                red_zone = True
            if writes(i, op, n) and not i.mnemonic.startswith(NO_ACCESS) and \
                    op.mem.base not in (RSP, RIP) and not (rbp is not None and op.mem.base == RBP):
                stores.append(i)
    if not red_zone or not stores:
        return None
    runs, run = [], []
    for i in insns:
        if not movable(i) or (i.address in targets and run):
            if run:
                runs.append(run)
            run = [i] if movable(i) else []
            continue
        run.append(i)
    if run:
        runs.append(run)
    store_at = {i.address for i in stores}
    sites = []
    for run in runs:
        marked = [k for k, i in enumerate(run) if i.address in store_at]
        if not marked:
            continue
        first, last = marked[0], marked[-1]
        while sum(i.size for i in run[first:last + 1]) < 5 and (last + 1 < len(run) or first > 0):
            if last + 1 < len(run):
                last += 1
            else:
                first -= 1
        span = run[first:last + 1]
        if sum(i.size for i in span) >= 5:
            sites.append([span[0].address, b''.join(i.bytes for i in span).hex(),
                          [-i.size if i.address in store_at else i.size for i in span]])
    covered = {a for s in sites for a in range(s[0], s[0] + len(s[1]) // 2)}
    return sites, [f'{i.address:#x}' for i in stores if i.address not in covered]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('game', type=Path)
    p.add_argument('--update', type=Path)
    p.add_argument('-o', '--out', type=Path, default=ROOT / 'patches' / 'redzone.json')
    a = p.parse_args()
    view = game_view.open_view(a.game, a.update)
    spu = sorted(f'gamedata_orbis/spu/{x.name}' for x in (view / 'gamedata_orbis/spu').glob('*.prx'))
    modules = ['libc.prx', 'libSceFios2.prx', *spu]  # as run.py links them
    image, table = linked(view, modules)
    objects = [('eboot.bin', (view / 'eboot.bin').read_bytes(), 0)]
    for m in table:
        path = view / m['file'] if '/' in m['file'] else view / 'sce_module' / m['file']
        objects.append((m['file'], Path(str(path)).read_bytes(), int(m['base'], 16) if isinstance(m['base'], str) else m['base']))
    changed = lbppc_changes()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    sites, functions, skipped, uncovered, per_object = [], 0, [], [], {}
    for name, data, base in objects:
        starts = function_starts(image, data, base)
        found_here = 0
        for start, end in zip(starts, starts[1:]):
            code = image[start:end]
            if not (RED_ZONE_RSP.search(code) or FRAME_POINTER in code):
                continue
            result = analyse(md, image, start, end)
            if result is None:
                continue
            found, missing = result
            if missing == ['jump table']:
                skipped.append(f'{start:#x} {name} (jump table)')
                continue
            kept = []
            for site in found:
                lo, hi = site[0] - KEEP_CLEAR, site[0] + len(site[1]) // 2 + KEEP_CLEAR
                if any(s < hi and e > lo for s, e in changed):
                    skipped.append(f'{site[0]:#x} {name} (next to an LBPPC code change)')
                    continue
                kept.append(site)
            functions += 1
            sites += kept
            found_here += len(kept)
            uncovered += missing
        per_object[name] = found_here
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(dict(
        game='LittleBigPlanet 3 1.28 (CUSA00473; the European CUSA00063 has the same code), linked with '
             + ', '.join(modules),
        image_sha256=hashlib.sha256(image).hexdigest(), functions=functions, per_object=per_object,
        skipped=skipped, uncovered_stores=uncovered, sites=sites), separators=(',', ':')) + '\n', encoding='ascii')
    print(f'{len(sites)} sites in {functions} red-zone leaf functions ({per_object}); {len(uncovered)} stores '
          f'uncovered; {len(skipped)} skipped -> {a.out}')


if __name__ == '__main__':
    main()

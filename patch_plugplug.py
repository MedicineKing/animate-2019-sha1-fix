#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlugPlugOwl 9.2.1.39 SHA-1 crash fix
Adobe Animate CC 2019 (19.1.0.349) crashes on startup on CPUs with SHA-NI
support. Faulting module: PlugPlugOwl.dll 9.2.1.39, 0xc0000005 @ 0x19d63.

Root cause: in the SHA-NI-optimized SHA-1 epilogue, `rax` (saved stack
pointer) is clobbered by the main loop; the epilogue then restores a garbage
stack pointer and `ret` jumps into the wild.

This script patches YOUR OWN copy of PlugPlugOwl.dll in place (backup first).
No Adobe binaries are distributed here.

Usage:
    python patch_plugplug.py [path-to-PlugPlugOwl.dll]
    default: C:\\Program Files\\Adobe\\Adobe Animate CC 2019\\PlugPlugOwl.dll
"""
import hashlib
import shutil
import sys
from pathlib import Path

DEFAULT = r"C:\Program Files\Adobe\Adobe Animate CC 2019\PlugPlugOwl.dll"

# 原始出口序列: pshufd x2 + movdqu/movd (digest 写出) + 4x movaps [rax-*] + mov rsp,rax
ANCHOR_OLD = bytes.fromhex(
    "f30f7f07660f7e4f10"                     # movdqu [rdi],xmm0 ; movd [rdi+10h],xmm1
    "0f2870b80f2878c8440f2840d8440f2848e8"   # movaps xmm6..9,[rax-48/-38/-28/-18h]
    "4889c4"                                 # mov rsp,rax
)

# 修复后的出口: rsp 相对寻址, 37 字节
PATCH_NEW = bytes.fromhex(
    "0f103424"        # movups xmm6,[rsp]
    "0f107c2410"      # movups xmm7,[rsp+10h]
    "440f10442420"    # movups xmm8,[rsp+20h]
    "440f104c2430"    # movups xmm9,[rsp+30h]
    "4883c448"        # add    rsp,48h
    "488b7c2408"      # mov    rdi,[rsp+8]
    "488b742410"      # mov    rsi,[rsp+10h]
    "f3c3"            # rep ret
)


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT)
    if not path.exists():
        print("ERROR: file not found:", path)
        return 1

    data = path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()

    new_anchor = ANCHOR_OLD[:9] + PATCH_NEW
    if data.count(new_anchor) == 1:
        print("Already patched. Nothing to do.")
        return 0

    n = data.count(ANCHOR_OLD)
    if n != 1:
        print(f"ERROR: anchor not found uniquely (matched {n} time(s)).")
        print("This DLL may be a different version or already modified.")
        print("Known good md5: 06a4680e91fa65a40f26253696255f40 (9.2.1.39)")
        return 1

    i = data.find(ANCHOR_OLD)
    start = i + 9  # 指向 4x movaps 起点
    out = bytearray(data)
    out[start:start + len(PATCH_NEW)] = PATCH_NEW

    backup = path.with_name(path.name + ".bak")
    if not backup.exists():
        shutil.copy(path, backup)
    path.write_bytes(bytes(out))

    print(f"OK. patched at file offset 0x{start:x} ({len(PATCH_NEW)} bytes)")
    print(f"    md5 before: {md5}")
    print(f"    backup:     {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

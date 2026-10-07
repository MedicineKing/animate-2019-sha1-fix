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
    python patch_plugplug.py                 # 自动探测安装目录
    python patch_plugplug.py <dll 路径>
    python patch_plugplug.py <安装目录>
    python patch_plugplug.py --force <路径>   # 强制修复未验证版本(风险自担)

默认仅修改 md5 与已知 9.2.1.39 原版一致的文件; 其它版本只报告, 不做改动。
"""
import hashlib
import shutil
import sys
from pathlib import Path

KNOWN_MD5 = "06a4680e91fa65a40f26253696255f40"  # 原版 9.2.1.39

# 原始出口序列: movdqu/movd (digest 写出) + 4x movaps [rax-*] + mov rsp,rax
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


def dll_version(data: bytes):
    """从 PE 版本资源读 FileVersion (搜 VS_FIXEDFILEINFO 签名)。"""
    sig = data.find(bytes.fromhex("bd04effe"))  # 0xFEEF04BD
    if sig < 0 or sig + 16 > len(data):
        return None
    ms = int.from_bytes(data[sig + 8:sig + 12], "little")
    ls = int.from_bytes(data[sig + 12:sig + 16], "little")
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def candidate_dlls():
    """自动探测: Adobe 注册表键 -> Uninstall 注册表 -> 常见路径。"""
    cands = []

    def add_dir(d):
        if not d:
            return
        p = Path(str(d).strip().strip('"'))
        if p.is_dir():
            dll = p / "PlugPlugOwl.dll"
            if dll.is_file() and dll not in cands:
                cands.append(dll)

    try:
        import winreg

        # 1) Adobe 专属键: HKLM\SOFTWARE\Adobe\Animate\<ver>\ApplicationPath
        for base in (r"SOFTWARE\Adobe\Animate", r"SOFTWARE\WOW6432Node\Adobe\Animate"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            sub = winreg.EnumKey(k, i)
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub) as sk:
                                try:
                                    add_dir(winreg.QueryValueEx(sk, "ApplicationPath")[0])
                                except OSError:
                                    pass
                        except OSError:
                            pass
            except OSError:
                pass

        # 2) Uninstall 键兜底
        for base in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            sub = winreg.EnumKey(k, i)
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub) as sk:
                                try:
                                    name = winreg.QueryValueEx(sk, "DisplayName")[0]
                                except OSError:
                                    continue
                                if "animate" not in name.lower():
                                    continue
                                for v in ("InstallLocation", "DisplayIcon"):
                                    try:
                                        add_dir(winreg.QueryValueEx(sk, v)[0].split(",")[0])
                                    except OSError:
                                        pass
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass  # 非 Windows 环境仅走常见路径

    # 3) 常见安装路径
    for drive in "CDEFGH":
        for sub in (r"Program Files\Adobe\Adobe Animate CC 2019",
                    r"Program Files (x86)\Adobe\Adobe Animate CC 2019",
                    r"Adobe\Adobe Animate CC 2019"):
            add_dir(f"{drive}:\\{sub}")
    return cands


def main() -> int:
    force = "--force" in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--force"]
    arg = argv[0] if argv else None
    if arg:
        p = Path(arg)
        targets = [p if p.suffix.lower() == ".dll" else p / "PlugPlugOwl.dll"]
    else:
        targets = candidate_dlls()
        if not targets:
            print("未自动找到 Animate 的 PlugPlugOwl.dll。")
            print('请手动指定: python patch_plugplug.py "D:\\Adobe\\Adobe Animate CC 2019"')
            return 1
        print(f"自动发现 {len(targets)} 个候选:\n")

    fixed = 0
    for dll in targets:
        if not dll.is_file():
            print(f"[跳过] 不存在: {dll}")
            continue
        data = dll.read_bytes()
        ver = dll_version(data) or "未知"
        new_anchor = ANCHOR_OLD[:9] + PATCH_NEW
        if data.count(new_anchor) == 1:
            state = "已修复"
        elif data.count(ANCHOR_OLD) == 1:
            state = "可修复"
        else:
            state = "未知/版本不匹配"
        print(f"[{state}] {dll}")
        print(f"           版本 {ver}  (md5 {hashlib.md5(data).hexdigest()})")
        if state != "可修复":
            continue
        if hashlib.md5(data).hexdigest() != KNOWN_MD5 and not force:
            print("           跳过: 非已验证版本(9.2.1.39)。确认要修复请加 --force")
            continue
        i = data.find(ANCHOR_OLD)
        start = i + 9
        out = bytearray(data)
        out[start:start + len(PATCH_NEW)] = PATCH_NEW
        backup = dll.with_name(dll.name + ".bak")
        if not backup.exists():
            shutil.copy(dll, backup)
        dll.write_bytes(bytes(out))
        print(f"           => 已修复 (offset 0x{start:x}, 37 字节; 备份 {backup.name})")
        fixed += 1

    print()
    print(f"完成, 修复 {fixed} 处。" if fixed else "没有需要修复的目标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

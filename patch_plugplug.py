#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Adobe CC 2019 SHA 病株修复工具

覆盖两个已知病株——同一份 2018 年的 OpenSSL SHA 代码被 Adobe 打包进了两个
不同的库,在支持 SHA-NI 指令集的新 CPU(约 2021 年起的桌面平台)上执行即崩:

  1. PlugPlugOwl.dll 9.2.1.39
       -> Adobe Animate CC 2019 启动闪退/秒退
  2. ZXPSignLib-minimal.dll (Premiere Pro CC 2019 13.x 打包版)
       -> Adobe Premiere Pro CC 2019 窗口都不出/启动即崩

根因(两病株指令级相同):出口序列把被主循环污染的数据指针当栈指针恢复,
`ret` 弹向野地址 -> 0xc0000005。修复 = 将 37 字节出口重写为 rsp 相对寻址,
与原设计意图完全一致(循环体内 rsp 恒定,已验证)。

本脚本只修改你自己机器上的拷贝(先备份)。不包含任何 Adobe 二进制。

用法:
    python patch_plugplug.py                 # 自动探测安装目录
    python patch_plugplug.py <dll 路径>
    python patch_plugplug.py <安装目录>       # 目录下两个病株文件都会被检查
    python patch_plugplug.py --force <路径>   # 强制修复未验证版本(风险自担)

默认只修改 md5 与已知原版一致的文件;其它版本只报告,不做改动。
"""
import hashlib
import shutil
import sys
from pathlib import Path

# 已验证原版白名单: 文件名 -> 允许的 md5 集合
KNOWN_MD5 = {
    # PlugPlugOwl 9.2.1.39 (Animate CC 2019, 4,605,968B)
    "PlugPlugOwl.dll": {"06a4680e91fa65a40f26253696255f40"},
    # ZXPSignLib-minimal.dll (Premiere Pro CC 2019 13.1.5.47, 2,032,728B)
    "ZXPSignLib-minimal.dll": {"3b65390b22c849e5cf28686233f79ada"},
}

TARGET_NAMES = ("PlugPlugOwl.dll", "ZXPSignLib-minimal.dll")

# 原始出口序列: movdqu/movd (digest 写出) + 4x movaps [rax-*] + mov rsp,rax
ANCHOR_OLD = bytes.fromhex(
    "f30f7f07660f7e4f10"                     # movdqu [rdi],xmm0 ; movd [rdi+10h],xmm1
    "0f2870b80f2878c8440f2840d8440f2848e8"   # movaps xmm6..9,[rax-48/-38/-28/-18h]
    "4889c4"                                 # mov rsp,rax
)

# 修复后的出口: rsp 相对寻址, 37 字节(两病株通用)
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

    def add_file(p):
        p = Path(p)
        if p.is_file() and p.name in TARGET_NAMES and p not in cands:
            cands.append(p)

    def add_dir(d):
        if not d:
            return
        base = Path(str(d).strip().strip('"'))
        if base.is_dir():
            for n in TARGET_NAMES:
                add_file(base / n)

    try:
        import winreg

        # 1) Adobe 专属键: ApplicationPath
        for base in (r"SOFTWARE\Adobe\Animate", r"SOFTWARE\WOW6432Node\Adobe\Animate",
                     r"SOFTWARE\Adobe\Premiere Pro", r"SOFTWARE\WOW6432Node\Adobe\Premiere Pro"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            sub = winreg.EnumKey(k, i)
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub) as sk:
                                for v in ("ApplicationPath", "InstallPath"):
                                    try:
                                        add_dir(winreg.QueryValueEx(sk, v)[0])
                                    except OSError:
                                        pass
                        except OSError:
                            pass
            except OSError:
                pass

        # 2) Uninstall 键兜底 (animate / premiere)
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
                                low = name.lower()
                                if "animate" not in low and "premiere" not in low:
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
                    r"Adobe\Adobe Animate CC 2019",
                    r"Program Files\Adobe\Adobe Premiere Pro CC 2019",
                    r"Program Files (x86)\Adobe\Adobe Premiere Pro CC 2019",
                    r"Adobe\Adobe Premiere Pro CC 2019"):
            add_dir(f"{drive}:\\{sub}")
    return cands


def main() -> int:
    force = "--force" in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--force"]
    arg = argv[0] if argv else None
    if arg:
        p = Path(arg)
        if p.is_file():
            targets = [p]
        else:
            targets = [p / n for n in TARGET_NAMES if (p / n).is_file()]
        if not targets:
            print(f"在 {arg} 下没找到 PlugPlugOwl.dll / ZXPSignLib-minimal.dll。")
            return 1
    else:
        targets = candidate_dlls()
        if not targets:
            print("未自动找到病株 DLL(PlugPlugOwl.dll / ZXPSignLib-minimal.dll)。")
            print('请手动指定: python patch_plugplug.py "D:\\Adobe\\Adobe Premiere Pro CC 2019"')
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
        known = KNOWN_MD5.get(dll.name, set())
        if hashlib.md5(data).hexdigest() not in known and not force:
            print("           跳过: 非已验证版本。确认要修复请加 --force")
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

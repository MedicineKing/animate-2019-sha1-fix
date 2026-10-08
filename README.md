# Adobe CC 2019 启动崩溃修复 — Animate & Premiere Pro

**TL;DR (EN)**: Adobe CC 2019 apps crash on launch on CPUs with SHA-NI support — both
Adobe Animate CC 2019 (`PlugPlugOwl.dll` 9.2.1.39, `0xc0000005 @ 0x19d63`) and Adobe
Premiere Pro CC 2019 (`ZXPSignLib-minimal.dll`, `0xc0000005 @ 0x73e3`). Root cause: the
same 2018-era OpenSSL SHA-1 code was bundled into both libraries; its SHA-NI-optimized
epilogue has a register-allocation bug — `rax` (saved stack pointer) is clobbered by the
main loop, then `mov rsp,rax` restores a garbage stack pointer and `ret` jumps into the
wild. This repo ships a byte-level patch script for **your own copy** of the DLLs (no
Adobe binaries included).

## 症状

**Animate CC 2019**：
- 启动即闪退（秒级到十几秒）
- 事件查看器：错误模块 `PlugPlugOwl.dll` 9.2.1.39，异常代码 `0xc0000005`，错误偏移 `0x19d63`

**Premiere Pro CC 2019**：
- 打开后窗口一直不出现（进程在、界面不出），或启动即崩
- 事件查看器 / WER：错误模块 `ZXPSignLib-minimal.dll`，异常代码 `0xc0000005`，错误偏移 `0x73e3`

共同特征：老 CPU（不支持 SHA-NI 指令集）不受影响 →“教室老电脑能跑、新装机全军覆没”的经典现象。

## 根因

两个 DLL 里内嵌了**同一份 2018 年的 OpenSSL SHA-1 实现**（PlugPlugOwl 是插件框架，ZXPSignLib 是 ZXP 扩展签名校验库——Adobe 把它们打包进了不同产品）。
它用 **SHA-NI**（`sha1rnds4` / `sha1nexte`）指令优化，入口把栈指针存进 `rax`，主循环却把同一个寄存器当临时寄存器用，出口又拿它恢复栈：

```asm
; 序言: 保存栈指针到 rax
19ACA: mov    rax,rsp
; 主循环: 每轮无条件覆盖 rax (真凶)
19B43: lea    rax,[rsi+40h]    ; cmovne 只保护 rsi, 不保护 rax
; 出口: 用被污染的值恢复栈
19D63: movaps xmm6,[rax-48h]   ; 崩点①: 从错误地址读寄存器 (0xc0000005)   ← Pr 崩在此处 (0x73e3)
...
19D75: mov    rsp,rax          ; rsp = 数据指针, 完全错误
19D82: rep ret                 ; 崩点②: 弹出野地址返回
```

只要 CPU 支持 SHA-NI，执行到该函数必崩；不支持 SHA-NI 的 CPU 走软件回退、根本不执行它，
所以该 bug 长期未被发现（这段路径 2018 年写下时，桌面 CPU 普遍还没有 SHA-NI 扩展）。

## 修复

出口序列整段重写为 rsp 相对寻址（主循环已验证不触碰 rsp，rsp 全程等于「入口 rsp − 0x48」）：

```asm
movups xmm6,[rsp]        ; 原 [rax-48h]
movups xmm7,[rsp+10h]    ; 原 [rax-38h]
movups xmm8,[rsp+20h]    ; 原 [rax-28h]
movups xmm9,[rsp+30h]    ; 原 [rax-18h]
add    rsp,48h
mov    rdi,[rsp+8]
mov    rsi,[rsp+10h]
rep ret
```

37 字节，借用尾部 `nop` 填充区，不影响其它任何代码。
xmm 槽位与序言保存严格对称，rsp 恢复后 rdi/rsi 照常从原位置读回——与原设计意图一致。

同一对锚点+补丁**两个病株通用**：Pr 的 ZXPSignLib 中该出口原生带 `mov rdi/mov rsi/rep ret`
尾段，与补丁 37 字节结构完全吻合，零修改直套。

## 用法

```bash
python patch_plugplug.py                     # 自动探测安装目录(注册表 + 常见路径, 扫两个病株)
python patch_plugplug.py <DLL 或目录路径>     # 手动指定
```

输出会列出每个候选 DLL 的**版本号**与状态：

```
自动发现 3 个候选:

[已修复] C:\Program Files\Adobe\Adobe Animate CC 2019\PlugPlugOwl.dll
           版本 9.2.1.39  (md5 e34ba450...)
[可修复] C:\Program Files\Adobe\Adobe Premiere Pro CC 2019\ZXPSignLib-minimal.dll
           => 已修复 (offset 0x67e3, 37 字节; 备份 ZXPSignLib-minimal.dll.bak)
```

- **版本检测**：只有 md5 与已知原版一致的文件才会被自动修改（PlugPlugOwl 9.2.1.39 =
  `06a4680e...`；ZXPSignLib-minimal Pr 2019.13.x = `3b65390b...`）；其它版本只报告、不动手
- 自动备份同目录 `.bak`（已存在则不覆盖）
- 唯一锚点定位，不硬编码偏移；不匹配直接拒绝；幂等（已修补的检测为「已修复」）

> 2018–2020 世代的其它 Adobe CC 产品可能打包了同款故障代码。若检测到锚点匹配但版本
> 未经验证，可在**自行备份确认**的前提下用 `--force` 尝试（不同构建的上下文可能不同，风险自担）。

## 为什么不用“删文件大法”？

网上流传的 Pr 修法是重命名/删除 `ZXPSignLib-minimal.dll` 来绕过启动崩溃。它能开软件，但属于
拆件——扩展签名校验功能随之失效。本补丁是**等价修复**：功能全保留，只修正出错的那条指令序列。

## 免责

- 本仓库**不包含任何 Adobe 二进制文件**，脚本仅对你自己合法安装的软件做兼容性修复
- 修改后数字签名失效属预期；操作前脚本会自动备份
- 仅用于解决“旧版软件在新硬件上无法运行”的兼容性问题

## 背景

2026-10 在一台 i9-14900K（Windows 10）机器上完成定位并实测：

- **Animate CC 2019**：修复后正常启动、进程稳定运行，零崩溃事件
- **Premiere Pro CC 2019**：修复前 WER 两次崩溃记录（同偏移 `0x73e3`）、窗口都不出；
  修复后窗口正常出现、进程稳定
- 两份 DLL 的崩溃指令级相同（`movaps xmm6,[rax-48h]`）——同一份病代码，被 Adobe 打包进了多个库

定位过程中收集到的公开报告（如知乎相关提问，错误偏移同为 `0x19d63`）显示这并非孤例。

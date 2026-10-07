# animate-2019-sha1-fix — Adobe Animate CC 2019 启动崩溃修复

**TL;DR (EN)**: Adobe Animate CC 2019 (19.1.0.349) crashes on launch on CPUs with SHA-NI support
(PlugPlugOwl.dll 9.2.1.39, `0xc0000005 @ 0x19d63`). Root cause: a register-allocation bug in its
SHA-NI-optimized SHA-1 epilogue — `rax` (saved stack pointer) is clobbered by the main loop, then
`mov rsp,rax` restores a garbage stack pointer and `ret` jumps into the wild. This repo ships a
byte-level patch script for **your own copy** of the DLL (no Adobe binaries included).

## 症状

- Animate 2019 启动即闪退（秒级到十几秒）
- 事件查看器：错误模块 `PlugPlugOwl.dll` 9.2.1.39，异常代码 `0xc0000005`，错误偏移 `0x19d63`
- 老 CPU（不支持 SHA-NI 指令集）不受影响 →“教室老电脑能跑、新装机全军覆没”的经典现象

## 根因

PlugPlugOwl 内含一份用 **SHA-NI**（`sha1rnds4` / `sha1nexte`）指令优化的 SHA-1 实现。
入口把栈指针存进 `rax`，主循环却把同一个寄存器当临时寄存器用，出口又拿它恢复栈：

```asm
; 序言: 保存栈指针到 rax
19ACA: mov    rax,rsp
; 主循环: 每轮无条件覆盖 rax (真凶)
19B43: lea    rax,[rsi+40h]    ; cmovne 只保护 rsi, 不保护 rax
; 出口: 用被污染的值恢复栈
19D63: movaps xmm6,[rax-48h]   ; 崩点①: 从错误地址读寄存器 (0xc0000005)
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

## 用法

```bash
python patch_plugplug.py "C:\Program Files\Adobe\Adobe Animate CC 2019\PlugPlugOwl.dll"
```

- 自动备份同目录 `PlugPlugOwl.dll.bak`
- 用 46 字节唯一锚点定位，不硬编码偏移；锚点不匹配会拒绝执行
- 幂等：已修补的 DLL 提示 “Already patched”

已知适用版本：**9.2.1.39**（Animate 19.1.0.349，原版 md5 `06a4680e91fa65a40f26253696255f40`）。
2018–2020 世代的其它 Adobe CC 产品若使用同款 PlugPlugOwl，可先跑脚本试（锚点匹配即可）。

## 免责

- 本仓库**不包含任何 Adobe 二进制文件**，脚本仅对你自己合法安装的软件做兼容性修复
- 修改后数字签名失效属预期；操作前脚本会自动备份
- 仅用于解决“旧版软件在新硬件上无法运行”的兼容性问题

## 背景

2026-10 在一台 i9-14900K（Windows 10）机器上完成定位并实测修复：
修复后 Animate 2019 正常启动、进程稳定运行，零崩溃事件。
定位过程中收集到的公开报告（如知乎相关提问，错误偏移同为 `0x19d63`）显示这并非孤例。

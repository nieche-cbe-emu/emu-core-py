# emu-core-py

CoolBar `.cbe` 模拟核心的 Python 参照实现。用于开发与差分验证，不是发布产物；
三端外壳运行的是 [emu-core-rs](https://github.com/nieche-cbe-emu/emu-core-rs)。

两份实现共享同一份接口契约，逐帧比对画面、宿主调用序列与调用次数；
行为分歧以本实现为准。

## 在开发流程中的位置

本实现不是 Rust 实现的历史遗留，而是仍在使用的三样东西：

**1. 宿主接口规格的来源。** `tools/genspec.py` 从固件 DWARF 生成
`emu/vmspec.py`（1019 行，已入库），`tools/genspec_rs.py` 再把它转成
`rust/emucore/src/vmspec.rs`。Rust 侧的槽位表由本仓库派生，不直接读固件——
带 DWARF 的固件是私有资产，公开仓库与 CI 都取不到。

**2. 逆向工作的运行环境。** `emu-tools` 中 16 个工具直接 import
`emu.runtime.Runtime` / `emu.machine.Machine` / `emu.host.Session`，
依赖本实现暴露的解释器内部状态：

| 出口 | 用途 |
|---|---|
| `Machine.log_calls` / `call_log` | 逐条记录宿主调用，`tools/tracecalls.py` 据此定位分叉点 |
| `Machine.where(addr)` | 地址还原为模块名与偏移 |
| `Runtime.report_unimpl()` | 按调用次数列出未实现的宿主 API |
| `Runtime.report_null_calls()` | 列出调用空函数指针的位置 |
| `Runtime.report_errors()` | 列出宿主实现内部报错，提示参数解读有误 |

新的宿主 API 与模块行为先在这里跑通——可随处插入打印、可在 `pdb` 中逐步执行、
改动无需编译——确认后再移植到 Rust。

**3. 差分测试的对照组。** `spec/baseline` 由 `tools/baseline.py` 驱动本实现生成，
四层差分（`rsdiff` / `ffidiff` / `enginediff` / `sessdump --native`）
均以本实现为基准。只保留单一实现时，该实现的缺陷没有独立的第二意见可比对。

## 特性

- 容器与资源解码：两种头（`magic` 4 / 8）、多包容器、图片（原始 RGB565 / GIF 变体 / PNG）、9 位距离 LZ77 脚本流
- 两代模块入口 ABI：新 SDK 的 screen 模型与老 SDK 的数字系统调用
- 约 250 个宿主 API，按固件符号名注册
- `emu/host.py` 的 `Session` 与 Rust 侧 `session.rs` 逐条对齐，差分以此为基础

## 环境要求

- Python 3.9 及以上
- `unicorn`、`capstone`

```bash
pip install unicorn capstone
```

## 快速开始

```python
from emu.host import Session

s = Session("game.cbe").boot()
s.set_keys(1 << 0)          # 按键是位掩码
px = s.step()               # 返回 RGB565 帧缓冲
w, h = s.size
s.stop()                    # 必须调用：模块在 AppStop 里落存档
```

## `Session` 接口

| 方法 / 属性 | 说明 |
|---|---|
| `Session(path, audio=True, budget=40_000_000)` | 构造，不启动 |
| `.boot()` | 引导模块并进入主循环 |
| `.stop()` | 运行模块的 `AppStop` |
| `.step(now=None)` | 推进一帧，返回 RGB565 帧缓冲；`now` 为秒，仅差分时传 |
| `.set_keys(mask)` | 当前按住的 32 位掩码 |
| `.set_touch(x, y, state)` | `state` 取 `down` / `move` / `up` |
| `.soft_key(side)` | `side` 取 `left` / `right` |
| `.take_events()` | 取走并清空事件，每条含 `kind`：`audio` / `exit` / `log` |
| `.take_events_json()` | 同上，JSON 字符串 |
| `.size` / `.name` / `.screens` / `.nonblank` | 尺寸、模块名、屏幕栈深度、非黑像素数 |

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `NIECHE_HOME` | `~/.nieche-emu` | 数据根：存档、模块虚拟文件系统、落盘的音频 |
| `NIECHE_FSBASE` | `assets/fatfs` | 虚拟文件系统的只读底层 |

## 目录

```
emu/       机器、运行时、宿主 API、显示、音频、字库、Session
cbelib/    .cbe 容器解析与资源解码
```

## 测试

差分与回归工具在 [emu-tools](https://github.com/nieche-cbe-emu/emu-tools)：

```bash
tools/batch.py        # 全语料回归
tools/rsdiff.sh       # 与 Rust 实现逐帧差分
tools/ci.sh           # 全部验收项
```

## 说明

本仓库只包含代码。游戏数据、手机固件与真机文件系统不在此处，也不提供。

## 许可证

GPL-2.0，见 LICENSE。第三方组件逐项见 THIRD-PARTY.md。

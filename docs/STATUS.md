# HD2 武器数值编辑器 — 进度

更新：2026-09-19

## 这是什么

读取《绝地潜兵 2》当前版本的武器数据表，在 GUI 里改数值，**生成一个 Mod**；
运行时把新数值写进游戏内存，且**只影响被改的那把枪**。

目标示例：R-4 鬣狗 220/45/AP3 → **400/200/AP4**

## 现状

| 部分 | 状态 |
|---|---|
| 数据层（伤害/弹种/爆炸三表 + 98 武器映射） | ✅ 完成 |
| GUI 展示与编辑 | ✅ 完成 |
| **Mod 生成器** | ✅ 完成 |
| **运行时写入层** | ✅ 完成（离线验证） |
| 游戏内验收 | ⏳ 待用户执行 |

## 怎么用

```
启动 GUI：  python gui/app.py     → http://127.0.0.1:8777
命令行生成： python tools/gen_mod.py --weapon "R-4 Hyena" --damage 400 --durable 200 --ap 4
```

GUI 里选武器 → 改数值 → 点「生成 Mod」→ 用 Arsenal 导入生成的 ZIP。

**安装要求**：必须安装并启用 Bingus Shared Loader（v14 或更高版本）。

## 加载机制（关键）

游戏的启动链是死的，**引擎不扫描 `mods/` 目录**：

```
boot  →  require('core/wwise/lua/wwise_flow_callbacks')
```

所以 Mod 要运行，只能被某个已启动的 loader `require`。我们注册在：

```
mods/cowboybingus/wide_angle_stratagems   (hash 0x68FFEDFF4A775027)
```

这是 Bingus Loader（当前安装版本 v15）硬编码清单里的**第 3 项**，且当前空闲。

**兼容性边界（如实说明）**：

| 场景 | 结果 |
|---|---|
| 只装 Bingus Loader | ✅ 工作 |
| 只装 Codex Bridge | ❌ 不加载（它的清单不转发第三方） |
| 两个都装 | ✅ 工作，互不干扰 |

## 运行时写入的守卫

写入前必须过三关，任一不过就**拒绝写入并记日志**：

| 关卡 | 检查 | 失败含义 |
|---|---|---|
| **identity** | 记录的 type_id 对不对 | 找到的不是目标武器 |
| **writability** | 页面是否可写 | 版本变化或保护机制 |
| **baseline** | 值是否还是原始值 | **别的 Mod 改过了** |

设计原则：**缺失的改动可以恢复，错误的写入不能。**

## 数据层的重要发现

**① 伤害记录有两个编号，不能混用**

| | 范围 | 用途 |
|---|---|---|
| **position** | 0..633 | 数组下标，弹种表引用这个 |
| **type_id** | 4..639 | `+0` 里存的值，全局唯一 |

**634 行里有 519 行两者不等。** 混用的后果是「部分武器找不到记录」，而且 R-4 恰好两者都是 137，所以只有测别的武器才会暴露。

**② 榴弹的伤害不在弹道表里**

GL-21 的弹道记录是 `20/2`（撞击值），真正的 `400/400` 在**爆炸表**。按弹道表改会「看起来生效、实际几乎没变化」。

**98 把武器里有 33 把是爆炸载荷。**

**③ 有 8 条共享伤害行**

例如 `damage[104]` 同时被 AR-23 Liberator、Liberator Carbine、M-105 Stalwart、StA-52 使用 —— 改它会影响 4 把枪。**生成前会弹确认框列出共用的其他武器**，用户确认后才写入。

R-4 是**双向独占**的：弹种 #245 只被 R-4 用，`damage[137]` 只被弹种 #245 引用。

**④ 文件布局 = 运行时布局（已证）**

用 Codex Mod 的 `build.json` 当独立证人交叉验证：它的 `record_offset 0xf9dc` == 我们算的 `0x2c + 235×272`，**0 字节误差**，五项字段全中。

**含义：GUI 显示的行号可直接用于内存写入，无需额外探测。**

## 测试

```
tests/test_parse.py                  15 检查  数据表解析
tests/test_names.py                  31 检查  武器映射 + 交叉校验
tests/test_mod_packaging.py          10 检查  Lua 编译 + 归档
tests/test_layout_matches_runtime.py 15 检查  文件布局 = 运行时布局
tests/test_resolver.py               10 检查  内存定位 + 拒绝错误命中
tests/test_resolver_second.py         6 检查  换别的武器也能定位
tests/test_guard_write.py            22 检查  三重守卫 + 7 种失败路径
tests/test_generated_mod.py          13 检查  端到端：生成的 Mod 真的改了值
tests/test_gui.mjs                   17 检查  GUI API 契约
tests/test_gui_dom.sh                16 检查  真实浏览器渲染
```

**关键设计**：运行时写入层在**模拟内存镜像**上测试，不是直接在游戏里试错。
镜像 = 噪声 + 真实的 634 条记录 + 噪声，跑的是**真正会发布的那个 Lua 文件**。

`test_gui_dom.sh` 用 Chromium 的 `--dump-dom` 拿到**脚本执行后**的 DOM。
这一步不能省：API 数据全对时，面板仍可能是占位符（实际发生过）。

## 目录

```
E:\game\HD2WeaponEditor\
├── tools/
│   ├── parse_dlbin.py     伤害表（76 字节/条）
│   ├── build_map.py       弹种表（272 字节/条）+ 武器链路
│   ├── explosions.py      爆炸表（152 字节/条）
│   ├── wiki_names.py      wiki 抓取 + 四字段交叉校验
│   ├── fetch_strings.py   从游戏文件抽字符串表
│   └── gen_mod.py         Mod 生成器（Lua → 编译 → 打包）
├── mod_template/src/      运行时 Lua（4 个模块）
│   ├── 10_resolver.lua    内存定位
│   ├── 20_guard.lua       三重守卫
│   ├── 30_write.lua       写入 + 回读校验
│   └── 40_log.lua         日志
├── gui/                   Flask + 单页前端
├── data/                  解析产物
├── build/                 生成产物（ZIP + Lua 源码）
└── docs/
    ├── STATUS.md          本文件
    └── ACCEPTANCE.md      游戏内验收指南
```

## 复用资产（来自旧项目，已验证）

```
E:\game\HD2StratagemHotkey\tools\hd2archive.py   .patch_N 读写
E:\game\HD2StratagemHotkey\tools\ljcompile.py    用游戏自带 lua51.dll 编译
E:\game\HD2StratagemHotkey\tools\ljbc.py         LuaJIT 字节码解析
```

## 未决 / 风险

| 项 | 说明 |
|---|---|
| **GameGuard** | 内存写入是它重点检测的类别。**建议只单人，别打公开局** |
| **封号** | 改数值属于影响玩法，非纯外观 |
| **游戏更新** | 布局变了守卫会拦住（拒绝写入而非写坏），但需要重新验证 |
| **Codex 的 RVA 常量** | 提取不出（锁在无法解析的字节码里），所以内存基址是自己扫描定位的 |
| **未匹配 41 把武器** | 33 把无攻击段（近战/辅助），7 把名字是 wiki 专有标签，1 把 wiki 数据不全。**结构上不存在对应记录** |
| **P-11 是唯一 mismatch** | wiki 页面缺数值，游戏里是 1500/1500 AP7 |

## 下一步

1. **游戏内验收**（`docs/ACCEPTANCE.md`）—— 装 Mod、看 R-4 是否 400/200、确认其他武器没变
2. 若验收通过，可考虑：支持一次改多把武器、更多字段（射速/后坐力/弹匣）

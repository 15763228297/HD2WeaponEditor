# 修复游戏更新导致的 Mod 失效 + 加入新武器

> **For Hermes:** 分两阶段。阶段 A 是必须的修复（不改就完全不能用）；阶段 B 是新武器。
> 用户已批准后执行。

**目标：** 让工具在游戏 `1.8.45850` 上重新可用，并把本次更新加入的新武器纳入可编辑范围。

**当前状态：** 游戏从 `1.8.45317` 更新到 `1.8.45850`。离线数据层**已经**迁移到新解析器并重生成（工作区有未提交改动），但**运行时层完全没跟上** —— 生成的 Mod 用旧位置去新表里找，找不到，于是回退到全空间扫描，扫完也找不到（指纹对不上），所以进游戏一直卡且不生效。

---

## 已查明的事实（都有实测支撑）

### 1. 卡顿的原因：扫描的指纹在新表里不存在

日志（2026-09-23 17:01-17:04）：

```
record: position 137, type id 137      ← 旧数据的位置
static route unavailable (array_start: null; table_base: null)
  -> falling back to the address-space scan
NOT FOUND (search complete): pattern not found: scanned 5,630,246,630 bytes
settled after 1007 attempt(s)          ← 扫了 1000 次，5.6 GB
```

新表里 `type_id=137` 存在，但在 **position 142**（不是 137），值是 `180/50`。Mod 找的是「position 137 上的 type_id 137」，这个组合在新表里不存在 → 扫不到 → 卡到超时。

### 2. 静态路由失效：旧 RVA 读不到东西

```
rva 0x2ac7cb0 → null
rva 0x2791748 → null
```

两个 RVA 都在新 image 范围内（SizeOfImage = 0x4770000），但读出来是空 → 游戏重编译后指针位置变了。**需要重新探路**。

### 3. 一个隐藏的定时炸弹：ARRAY_START 现在是错的

这是本次调查最重要的发现，且**不修会静默写错 5 行**：

```
旧解析器：从文件偏移 480 开始解析（应为 100）→ 少算 5 行
  R-4 真实位置 142，旧解析器说 137
  Mod 算地址：table_base + 480 + 137*76 = table_base + 10892
  真实地址  ：table_base + 100 + 142*76 = table_base + 10892
  → 正好相等。旧常量恰好抵消了旧解析器的错误。

新解析器：从容器描述符读（偏移 100，正确）→ 位置是真的位置
  R-4 位置 147
  Mod 算地址：table_base + 480 + 147*76 = table_base + 11652
  真实地址  ：table_base + 100 + 147*76 = table_base + 11272
  → 差 380 字节 = 5 行。写下去会改错武器。
```

所以 `ARRAY_START` 必须从 `0x1e0`(480) 改成 `0x64`(100)。**这是 0x1e0 这个常量存在的唯一原因**——它当初就是为了抵消解析错误而调的。

### 4. 表结构变化

| 表 | 旧 | 新 | 变化 |
|---|---|---|---|
| 伤害 | 639 | 649 | +10 |
| 弹种 | 343 | 350 | +7 |
| 爆炸 | 413 | 422 | +9 |

**关键：弹种表发生了插入式位移，不只是追加。** 343 行里有 **261 行的名字变了**（比如 `40mm HEAT Grenade` 从 row 83 移到 row 96）。所以任何硬编码行号的做法都失效 —— 但工具用的是「名字→弹种」匹配，本来就不依赖行号，这一层是安全的。

伤害表的 type_id 也整体重编号了（delta 分布 +0 到 +10），546/649 能在旧表找到唯一对应。

### 5. 新武器：wiki 数据是有的，本地缓存过期了

GL-15 Evictor 的 wiki 页面在本地缓存里是「Weapon not found」，但**实时 wiki 已经有数据**：

```
GL-15 Evictor:  damage=490  AP=3  capacity=4  爆炸类  Pump-Action
P-34 Breacher:  damage=2000 AP=7
G-8 Immolation: damage=20
G-109 Urchin:   damage=0    AP=6
G-89 Smokescreen: damage=0
```

这 5 个页面需要重新抓取。**注意：GL-15 的 wiki 数值 490 在游戏伤害表里找不到对应行**（我扫了 400-600 范围，没有 490）—— 说明 wiki 这个数可能是「总伤害」而非「单段伤害」，或者 wiki 数据本身不准。这需要在阶段 B 里单独查。

### 6. 新增的表行（可能是新武器）

```
新增伤害行（10 条）:
  pos=438 type_id=645   750/750 AP5
  pos=638 type_id=640    15/15 AP3
  pos=639 type_id=641     0/0 AP10
  pos=641 type_id=642   150/50 AP3
  pos=642 type_id=643  3500/3500 AP8
  pos=643 type_id=644     7/7 AP5
  pos=644 type_id=646   700/700 AP3
  pos=645 type_id=647    50/50 AP4
  pos=646 type_id=648    25/25 AP2
  pos=648 type_id=649 10000/10000 AP10

新增弹种行（7 条）:
  row=344  120mm HE Cannon Round  -> 3500/3500 AP8
  row=345  75mm HEAT Grenade      -> 1300/1300 AP6
  row=346  (无名字) 150/50 AP3
  row=347  (无名字) 65/14 AP2
  row=349  (无名字) 42/11 AP3
  row=343/348 (无名字) -> 无伤害行
```

### 7. 在途改动（工作区未提交）

上一轮已经做完的：
- `tools/dlbin_tables.py`（新，204 行）—— 从容器描述符解析，不再用锚点搜索
- `tools/build_map.py` / `explosions.py` / `names.py` / `parse_dlbin.py` 已改用它
- `data/{damage_records,mapping,weapon_names}.json` 已重生成
- `.tools/old/` 保留了旧版 `.dl_bin`（重要，可做对照）

**未做完的：** 运行时层（`gen_mod.py`、`mod_template/src/*.lua`）、9 个失败的测试。

### 8. 失败的 9 个测试

```
test_parse                 锚点常量过期（ANCHOR_INDEX=137 等）
test_names                 R-4 damage index 期望 137，实际 142
test_layout_matches_runtime game.dll 哈希变了（Codex 的基线是旧版）
test_damage_reference      build_map.PROJECTILE_STATUS_SENTINELS 不存在
test_generated_mod         依赖旧位置
test_loader_handle_shape   同上
test_multi_weapon          同上
test_resolver_second       同上
test_settles_after_success 同上
```

---

## 阶段 A：修复运行时（必须，不做就完全不可用）

### Task A1：把 ARRAY_START 改成 100，并让它只有一个定义

**问题：** `0x1e0` 出现在 3 个地方，且现在语义是错的。

**文件：**
- `tools/gen_mod.py:28` — `ARRAY_START = 0x1e0`
- `mod_template/src/10_resolver.lua` — `M.ARRAY_START`
- `mod_template/src/16_static_chain.lua:67` — `M.ARRAY_START`

**做法：**
1. 三个值全部改成 `100`（`0x64`）
2. 加注释说明：这个值是 DLArray 描述符自带的 `offset` 字段，**不是**可以随手调的常量；旧值 480 是为了抵消旧解析器的 380 字节错误而设的
3. 在 `dlbin_tables.py` 里导出 `ARRAY_OFFSET_IN_PAYLOAD = 16`，让 Python 侧和 Lua 侧的来源能对上

**验证：**
```bash
python tests/test_layout_matches_runtime.py   # 修复后应通过
```
新增测试 `test_array_start_matches_container.py`：
- 从 `.dl_bin` 容器描述符读出真实数组偏移
- 断言 `gen_mod.ARRAY_START` 与之一致
- **变异验证：** 把值改回 480，测试必须变红

### Task A2：重新探路静态路由

**为什么必须重做：** 旧 RVA 读出来是 null，游戏重编译后指针位置变了。

**做法：**
1. 更新 `mod_template/src/15_static_route.lua` 的探针指纹 —— 它要找的行现在是 position 147 / type_id 142（`220/45 AP[3,3,3,0]`）
2. 打开 `gen_mod.py` 的 `PROBE_STATIC_ROUTE = true`
3. 生成一个探针 Mod，用户部署后进游戏
4. 日志会打印命中的 RVA
5. **跑两次**，确认 RVA 相同（跨启动固定才可用）
6. 把新 RVA 写进 `16_static_chain.lua` 的 `M.ROUTES`

**需要用户配合：** 两次进游戏（每次约 1-2 分钟）。这是唯一需要用户操作的部分。

**如果探针找不到：** 说明这个 build 的指针不在模块的可写段里。那么：
- 保留全空间扫描作为唯一路径（回到改造前的行为）
- 把 `WARMUP_FRAMES` 改成按时间（之前发现 180 帧实际只跑 1 秒，没起到避开加载高峰的作用）
- 向用户说明：卡顿会回来，但功能可用

**验证：** 日志出现 `static route hit: game.dll+0xXXXX -> array 0x...`，且两次 RVA 相同。

### Task A3：修 `test_parse.py` 的锚点常量

**问题：** 测试硬编码 `ANCHOR_INDEX=137`, `ANCHOR_DAMAGE=(220,45)`, `ANCHOR_AP=[3,3,3,0]`。新表里这些值还在（position 147），但测试用的是旧位置。

**做法：** 把锚点改成从**名字**推导而不是硬编码位置：
- 从 `weapon_names.json` 读 R-4 的 `damage_position`
- 用它去 `damage_records.json` 取期望值
- 断言解析器输出的那一行与之一致

这样游戏再更新时，测试跟着数据走，不用手改常量。

**变异验证：** 把解析起点改回 480，测试必须变红。

### Task A4：修其余 8 个测试

逐个看失败原因，大部分是「期望值写死成旧位置」。统一策略：**从 `weapon_names.json` 读位置，不硬编码**。

- `test_names.py` — R-4 期望 137 → 改成从数据读
- `test_layout_matches_runtime.py` — Codex 的基线是旧 build 的，game.dll 哈希对不上。**这个测试现在无法通过**，因为 Codex 还没更新。处理：检测到哈希不符时**明确 SKIP 并说明原因**，而不是 FAIL（它验证的是「我们的布局和 Codex 一致」，Codex 没跟上时这个验证前提就不成立）
- `test_damage_reference.py` — `PROJECTILE_STATUS_SENTINELS` 不存在，看是不是重构时删了
- `test_generated_mod.py` / `test_loader_handle_shape.py` / `test_multi_weapon.py` / `test_resolver_second.py` / `test_settles_after_success.py` — 依赖生成物里的旧位置

### Task A5：加一个「构建指纹」守卫

**为什么：** 这次的教训是「游戏更新后工具静默失效」。工具应该**主动发现**数据过期。

**做法：** 记录生成数据时对应的 game.dll 哈希，写进 `data/build_fingerprint.json`：

```json
{
  "game_dll_sha256": "...",
  "exe_version": "1.8.45850.0",
  "generated_at": "2026-09-23T17:00:00",
  "tables": {"damage": 649, "projectile": 350, "explosion": 422}
}
```

GUI 启动时对比当前 game.dll 哈希：
- 一致 → 正常
- 不一致 → 顶部显示警告横幅：「游戏已更新，数据可能过期，请重新生成数据」

**这不是可选功能** —— 没有它，下次更新用户又会遇到「进游戏卡 1 分钟然后什么都没发生」，且不知道原因。

**测试：** `test_build_fingerprint.py`，变异验证（改哈希后必须报警告）。

### Task A6：重新生成 Mod 并验证

1. 用修好的工具生成 R-4 的 Mod（400/200/AP7）
2. 检查生成物里的 `record_offset` 是 `100 + 147*76 = 11272`（不是 11652）
3. 用户部署，进游戏
4. 确认：不卡顿 + 数值生效

**这是唯一能证明修复成功的验证。**

---

## 阶段 B：加入新武器

### Task B1：重新抓取 wiki 页面

**已知需要重抓的 5 个页面：**
```
GL-15 Evictor      damage=490  AP=3
P-34 Breacher      damage=2000 AP=7
G-8 Immolation     damage=20
G-109 Urchin       damage=0    AP=6
G-89 Smokescreen   damage=0
```

做法：`python tools/wiki_names.py --fetch`（先确认这个 flag 存在）。注意 wiki 有速率限制，加延迟。

### Task B2：判断 GL-15 的 490 是什么

**问题：** wiki 说 490，但游戏伤害表里没有 490。可能：
- 490 是「直击 + 爆炸」的总和
- wiki 数据未经核实（页面标着 WIP）
- 490 对应的是直击段，爆炸段另算

**做法：**
1. 查 wiki 页面的完整数值表（不只 infobox）
2. 在游戏表里找爆炸类武器看这个数怎么拆
3. 如果对不上，**拒绝匹配并说明**（符合现有设计：对不上就不让改，而不是猜）

### Task B3：把能匹配的新武器加进映射

对每个新武器：
1. 名字 → 弹种行（靠 `ammo_raw` + 速度 + 伤害四字段交叉校验）
2. 弹种行 → 伤害行（`+60` 是 type_id，转 position）
3. 四字段校验通过才纳入

**已知会失败的：** 6 个火焰武器（`+60` 是 1/2/3 的状态引用，不是伤害行）—— 这些应该正确拒绝，不是 bug。

### Task B4：更新 README / release notes

- 武器数量变化
- 新增武器清单
- 已知问题（哪些新武器还改不了，为什么）

---

## 风险与权衡

| 风险 | 说明 | 缓解 |
|---|---|---|
| **探针找不到新 RVA** | 游戏可能改了指针存放方式 | 回退全空间扫描（功能可用但卡）；改暖机为按时间 |
| **GL-15 的 490 对不上游戏表** | wiki 可能不准 | 拒绝匹配并说明，不猜 |
| **新武器依赖 wiki 数据** | wiki 未更新完就加不了 | 只加能通过四字段校验的 |
| **ARRAY_START 改动影响所有武器** | 改错会让全部武器写错行 | 加测试 + 变异验证；先在 R-4 上实测 |
| **Codex 未更新** | `test_layout_matches_runtime` 失去独立见证 | 明确 SKIP 而非 FAIL |

## 需要用户做的

1. **阶段 A 的探针**：部署探针 Mod，进游戏**两次**，把日志给我
2. **阶段 A 的最终验证**：部署修好的 Mod，进游戏确认不卡 + 生效
3. 阶段 B 完成后，再进游戏验证新武器

## 交付顺序

```
A1 ARRAY_START 修正 + 测试        ← 不需要用户，我做完
A2 探针准备                        ← 我做完，等用户跑
A3-A5 测试修复 + 构建指纹守卫      ← 不需要用户，我做完
A6 最终验证                        ← 等用户跑
B1-B4 新武器                       ← 依赖 A 完成 + wiki 数据
```

---

## 未决问题（需要用户决定）

1. **探针要跑两次** —— 你能配合进两次游戏吗？如果只想跑一次，我可以只拿一组 RVA 先用着，但无法确认它跨启动固定。
2. **GL-15 的 490 对不上游戏表** —— 如果最后确认 wiki 数据不准，是先不加它，还是用一个能对上游戏表的相近行（不推荐，会改错）？
3. **旧版本的 Mod** —— 用户手上可能还有 v0.3.0 生成的 Mod，那些在更新后同样失效。要不要在 release 里说明「游戏更新后所有旧 Mod 都需要重新生成」？

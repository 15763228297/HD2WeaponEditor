# 计划：GUI 显示官方简体中文武器名

日期：2026-09-23
状态：**待审查**

## 目标

GUI 里的武器名改成**游戏官方简体中文**，只显示中文，不留英文小字。

```
现在：GL-15 Evictor
改成：GL-15 驱逐者
```

## 背景：为什么现在显示英文

GUI 显示的 `page` 字段是**英文名**（`GL-15 Evictor`），它有两个作用：

1. **显示**给用户看
2. **内部身份** —— 匹配武器、生成 Mod、跨表引用都用它

第 2 点是关键：**它不能被改掉**，否则整个匹配逻辑要重写。

## 根因（已查明）

你的游戏显示简体中文（`驱逐者`），但我们的数据里只有繁体（`驅離者`）。

**简体中文包一直在我们的提取结果里，但被构建脚本跳过了。**

`tools/fetch_strings.py` 的 `build()` 里：

```python
lang = (data.get("Language") or {}).get("KnownFriendlyName")
if not lang:
    # One pack has no Language block; skip rather than mislabel it.
    print(f"  skip (no language): {f.name}")
    continue
```

简体包的 `Language` 块长这样：

```json
{"Hash": "0x5942ccf7", "KnownName": "", "KnownFriendlyName": ""}
```

**只有哈希，没有语言名** → 被跳过。18 个文件、24,363 个键、4419 处简体专用字。

## 已实测的事实

**覆盖**：99/99 武器都能查到官方简体名。

```
AC-8 Autocannon        → AC-8 机炮
AR-23 Liberator        → AR-23 解放者
GL-15 Evictor          → GL-15 驱逐者
R-4 Hyena              → R-4 鬣狗
SG-225 Breaker         → SG-225 破裂者
GR-8 Recoilless Rifle  → GR-8 无后坐力炮
```

**歧义**：6 把武器的昵称在字符串表里对应多个键，给出**不同的**中文：

| 武器 | 候选 | 正确 |
|---|---|---|
| BR-14 Adjudicator | 裁决者 / 审判者 | 审判者 |
| FAF-14 Spear | 之矛 / 飞矛 | 飞矛 |
| G-16 Impact | 着陆 / 冲击弹 / 冲击 / 影响 | 冲击弹 |
| G-6 Frag | 破片弹 / 破片手雷 | 破片弹 |
| M-105 Stalwart | 盟友 / 轻机枪 | 盟友 |
| SMG-37 Defender | 捍卫者 / 防卫者 | 防卫者 |

**消歧方法（已验证）**：正确的候选**与型号键（如 `BR-14`）出现在同一批资源文件里**，错的不会。两条独立规则（同文件 / 大小写精确）在 94/99 上一致，不一致的 5 个用「同文件」规则解决（已逐个人工核对，结果正确）。

**我踩过的坑**：第一遍用「昵称查表」的简单做法，把 `G-16 Impact` 查成了「着陆」（错，撞上了另一个同名键）。**这个判别法是必需的，不是可选优化。**

## 改动方案

### 1. `tools/fetch_strings.py` —— 不再跳过简体包

```python
# 语言哈希 → 名字。0x5942ccf7 是简体中文：包里没有 KnownFriendlyName，
# 但内容是简体（含简体专用字，无繁体专用字），且它是唯一没有名字的包。
LANG_BY_HASH = {"0x5942ccf7": "Chinese (Simplified)"}

lang = (data.get("Language") or {}).get("KnownFriendlyName")
if not lang:
    lang = LANG_BY_HASH.get((data.get("Language") or {}).get("Hash"))
if not lang:
    print(f"  skip (unknown language): {f.name}")
    continue
```

**为什么用哈希而不是猜**：哈希是游戏自己给的稳定标识；猜「没名字的就是简体」在下次更新出现别的无名包时会静默标错。

### 2. `data/strings.json` —— 重建

多出 `Chinese (Simplified)` 一列。**这是派生数据**（不是游戏素材，和现在打包的 3 个文件一样）。

### 3. `tools/wiki_names.py` —— 生成中文名

新增一个模块 `tools/zh_names.py`：

```python
def zh_name(page: str, tables) -> str | None:
    """型号 + 昵称，各自查官方简体名。查不到返回 None（不猜）。"""
```

规则（已实测 99/99）：
1. 拆 `page` 成 `型号` + `昵称`
2. 两个键都必须在字符串表里存在
3. 候选有多个时，**只保留与型号键同处一批资源文件的**
4. 仍不唯一 → 返回 None（拒绝，不猜）

`wiki_names.py` 的 `match_all` 输出里加 `name_zh` 字段。

### 4. GUI 显示中文

`gui/app.py`：`api_weapons` 加 `name_zh` 字段。
`gui/templates/index.html`：
- 列表项：显示 `name_zh`，**没有则回落 `page`**
- 详情面板标题：同上
- 搜索：**同时匹配 `page` 和 `name_zh`** —— 你搜英文或中文都能找到

### 5. 打包

`name_zh` 在 `weapon_names.json` 里，**已经**在打包清单里 —— **不用改打包配置**。

## 不做什么

- **不改 `page`** —— 它是内部身份，匹配/生成/跨表引用全靠它
- **不留英文小字**（你的决定）
- **不碰繁体名**（数据里保留，GUI 不显示）

## 风险

**低。** 理由：

1. **纯显示层** —— `name_zh` 不参与任何匹配或写入逻辑
2. **`page` 不变** —— 生成路径一个字节都不动
3. **搜索双向** —— 改名后搜英文照样能搜到
4. **失败模式安全** —— 查不到中文名就显示英文名，不会显示错名字

**唯一残留风险**：某个武器的中文名查错了（比如又撞上同名键）。缓解：消歧规则已在 99/99 上验证；查不到的**拒绝**而不是猜。

## 验证计划

1. `tests/test_zh_names.py`（新）——
   - 99 把武器都有中文名
   - 6 个歧义案例全部正确（逐个断言，不是只数个数）
   - `page` 一个都没变
   - 查不到时返回 None 而不是猜
2. **变异验证**：故意改坏消歧规则 → 测试必须变红
3. `tests/test_gui_dom.sh` —— 加断言：列表显示中文名、搜索中英文都能命中
4. 全量回归（40 个 Python 套件 + Node + DOM）
5. **打包 EXE 实测**：GUI 里看到中文名，生成 Mod 仍指向正确行

## 工作量

约 1–2 小时（含测试和变异验证）。

## 待你确认

1. 方案可以吗？
2. 中文名格式 `GL-15 驱逐者`（型号 + 空格 + 名字）对吗？还是你想要别的（比如 `GL-15驱逐者` 无空格）？

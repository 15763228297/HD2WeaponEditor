# 游戏内验收指南

对象：`build/HD2-Weapon-Stat-Editor.zip`（R-4 Hyena → 400/200/AP4）

---

## 装之前先确认

| 项 | 要求 |
|---|---|
| Bingus Shared Loader v14 | **必须启用**，放在加载顺序**最后一位**（默认优先级下最后 = 最高优先级） |
| 游戏版本 | Steam build 24826606 / EXE 1.8.45317.0。**版本不符时守卫会拒绝写入**，不会写坏 |
| 建议场景 | **单人在线（Solo）**。别打公开局 |

生成的 Mod 会检查 `game.dll` 的 sha256（`CC75948D…557470C`）。构建信息在 ZIP 里的 `build-info.json`。

---

## 安装步骤

1. Arsenal → 导入 `HD2-Weapon-Stat-Editor.zip`
2. **启用**它
3. 确认 **Bingus Shared Loader v14 也启用**，且排在最后
4. **Purge 然后 Deploy**
5. 启动游戏

> 如果日志里 `wide_angle_stratagems` 没出现，说明 Loader 没加载我们。检查 Loader 是否启用、是否排在最后。

---

## 观察项

装好后**进任意任务**（飞船上的武器面板可能不刷新，以任务内为准）。

| # | 看什么 | 期望 | 不符时说明 |
|---|---|---|---|
| 1 | `%LOCALAPPDATA%\CowboyBingus\Helldivers2\Logs\WeaponEditor.log` | 文件存在，含 `== weapon: R-4 Hyena ==` | Loader 没加载我们 |
| 2 | 同一日志 | `located at 0x...` | 内存扫描失败 |
| 3 | 同一日志 | `verified: every requested field now holds its new value` | 写入或校验失败 |
| 4 | 同一日志 | **没有** `REFUSED` | 守卫拒绝了，日志会说明原因 |
| 5 | 游戏内 R-4 面板 | 伤害 **400**、耐久伤害 **200**、穿甲 **Heavy (AP4)** | |
| 6 | 打一枪 | 伤害明显高于原版 220 | |
| 7 | **其他武器** | 数值不变 | ← 这是「只改我这一把」的验收点 |
| 8 | 队友视角（如果联机） | 不受影响 | |

**第 7 项最重要。** 具体看这几把（和 R-4 同弹族的 9x70mm 系列）：

- R-4 的兄弟弹种：别的用 9x70mm 的枪
- `AR-23 Liberator`（它的记录**被 4 把枪共用**，本来就不该被我们碰）
- 任意其他武器

---

## 日志的正常样子

```
WeaponEditor log-v1
started 2026-09-19 ...
== HD2 Weapon Stat Editor ==
module loaded
game.dll build reference: CC75948D90FDFDE2...
game.dll base 0x7ff6...

== weapon: R-4 Hyena ==
record: position 137, type id 137
located at 0x7ff6...
applied: damage 220->400, durable 45->200, ap0 3->4, ap1 3->4, ap2 3->4
verified: every requested field now holds its new value
done
```

---

## 失败时把日志全文发我

守卫的拒绝是有信息量的，**不是**「mod 没生效」这种沉默失败。常见拒绝：

| 日志里的字样 | 含义 | 怎么办 |
|---|---|---|
| `record not found` | 内存里找不到这条记录 | 可能游戏更新改了布局 |
| `identity mismatch` | 找到的记录不是 R-4 的 | 同上 |
| `baseline mismatch` | **别的 Mod 已经改过这个字段** | 冲突，看是谁改的 |
| `record is not writable` | 页面不可写 | 版本或保护机制变化 |

**任何一种都意味着「什么都没改」** —— 这是设计如此，不是 bug。

---

## 卸载

Arsenal → 禁用这个 Mod → Purge → Deploy。

它只在内存里写数值，**不修改任何游戏文件**，所以卸载后立即恢复原样。

---

## 与 Codex Mod 的关系

你现在装着 Codex 的两个数值 Mod（`R-2124 Constitution`、`P-11 Self Heal`）。它们改的是 `damage[132]` 和 `damage[228]`，**与 R-4 的 `damage[137]` 不重叠**，所以可以共存。

但注意：**如果 Codex 以后也改了 R-4**，我们的 baseline 守卫会检测到并拒绝写入（而不是互相覆盖）。

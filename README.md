# 🎮 Steam Indie Tracker

每周自动追踪 Steam 爆款独立游戏，AI 拆解核心玩法组合，发现设计趋势。

## 效果预览

网站自动展示每周热门 indie 游戏的：
- 🏷️ **玩法标签**（如 Roguelike、卡牌构筑、节奏动作）
- 🧮 **玩法公式**（如 "节奏战斗 + 清版动作 + Roguelite"）
- 💡 **核心 Hook**（一句话说明为什么这个游戏有意思）
- 📈 **趋势分类**（如 "概率/赌博机制热"、"Roguelike变体热"）

## 快速部署

### 1. Fork 这个仓库

### 2. 开启 GitHub Pages
- 进入仓库 Settings → Pages
- Source 选 `main` branch, 目录选 `/ (root)`
- 保存，等几分钟网站就上线了

### 3. 配置自动更新（可选）
如果你想让数据每周自动更新：

1. 获取一个 [Anthropic API Key](https://console.anthropic.com/)
2. 在仓库 Settings → Secrets → Actions 中添加：
   - Name: `ANTHROPIC_API_KEY`
   - Value: 你的 API key
3. GitHub Actions 会每周二自动运行

**不配 API Key 也能跑**——脚本会用 Steam 标签做基础分析（`--no-ai` 模式）。

### 4. 手动运行
```bash
# 带 AI 分析
python scripts/fetch.py --anthropic-key sk-ant-xxx

# 不用 AI，纯标签分析
python scripts/fetch.py --no-ai
```

## 数据来源

| 来源 | 用途 | 免费？ |
|------|------|--------|
| Steam Weekly RSS | 每周 Top 10 榜单 | ✅ |
| Steam Search API | 热门新品列表 | ✅ |
| Steam AppDetails API | 游戏详情、类型、发行商 | ✅ |
| SteamSpy API | 用户投票标签 | ✅ |
| Claude API | 玩法智能分析 | 有免费额度 |

## 项目结构

```
├── index.html                  # 展示网站
├── data/
│   ├── weekly.json             # 当前周数据（自动更新）
│   └── archive_YYYY-MM-DD.json # 历史归档
├── scripts/
│   └── fetch.py                # 数据抓取+分析脚本
└── .github/workflows/
    └── update.yml              # 每周自动运行
```

## Indie 判定逻辑

脚本通过以下规则判断一款游戏是否为 indie：
1. Steam 是否标记了 "Indie" genre
2. 发行商是否在已知 AAA 大厂名单中
3. 价格是否在合理 indie 范围内（< $40）
4. 综合以上给出 high / medium / low 置信度

## License

MIT

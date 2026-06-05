# Steam Indie Tracker

每周自动追踪 Steam 本周火爆独立游戏，过滤 3A 和大厂发行作品，并分析玩法标签、核心 Hook、玩法公式和趋势分类。

网站是纯静态 GitHub Pages，数据由 GitHub Actions 自动生成到 `data/weekly.json` 和历史归档。

## 自动更新

`.github/workflows/update.yml` 会在每周二 UTC 10:00 自动运行，也可以在 GitHub Actions 页面手动点击 `Run workflow`。

可选：如果仓库 Secrets 里配置 `ANTHROPIC_API_KEY`，脚本会调用 Claude 做更细的中文玩法分析；没有 API Key 时会使用 Steam 标签的规则分析，仍可自动运行。

## 本地运行

```bash
python -m unittest discover -s tests -v
python scripts/fetch.py --no-ai
```

## 过滤逻辑

脚本从 Steam 周榜、热门新品、热销榜和趋势榜抓取候选，然后：

1. 读取 Steam AppDetails 和 SteamSpy 标签。
2. 排除 Valve、EA、Ubisoft、Sony、Microsoft、Tencent、NetEase、Square Enix 等 3A 或大厂发行/开发作品。
3. 保留 Steam 明确标记为 Indie，或无大厂背景且价格/标签符合小团队特征的作品。
4. 如果没有任何合格结果，自动任务失败并保留上一期数据，避免网站被覆盖为空。

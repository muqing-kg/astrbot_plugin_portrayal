<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_portrayal?name=astrbot_plugin_portrayal&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_portrayal

_✨ 人物画像插件 ✨_

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-muqing-kg-blue)](https://github.com/muqing-kg)

</div>

> 分析结果默认渲染为磨砂可爱风画像卡片图片发送（不再输出长文本正文）。

## 画像卡片包含什么

融合「本地统计 + LLM 解读」并渲染为图片：

- 本地特征：样本条数、均长、覆盖天数、风格、口头禅、活跃时段分布
- 结构化正文：一句话印象、群内人设、性格标签、语言风格、社交姿态、兴趣话题、性格特质、优势分析、缺点分析、相处建议、名场面与荣誉
- 平台适配：QQ / 微信群；微信隐藏账号类 chips，主要依赖实时消息缓存

## 介绍

根据群友聊天记录，调用 LLM 分析性格画像。

## 安装

- AstrBot 插件市场搜索 `astrbot_plugin_portrayal` 安装  
- 或克隆到插件目录：

```bash
cd /AstrBot/data/plugins
git clone https://github.com/muqing-kg/astrbot_plugin_portrayal
```

控制台重启 AstrBot。

## 配置

AstrBot 面板 → 插件管理 → astrbot_plugin_portrayal → 插件配置

## 指令

| 指令 | 说明 |
|:---:|:---:|
| `画像 @群友 <轮数>` | 综合性格画像（含优缺点与相处建议） |

轮数可省略，默认使用配置项 `default_query_rounds`。

提示词可在插件配置「提示词配置」中自定义；内置命令仅 `画像`。

### 平台说明

- **QQ（AIOCQHTTP）**：可回溯群历史消息
- **微信**：依赖机器人在线期间的实时消息缓存；首次使用前请先在本群正常挂机采集一段时间

## 注意事项

- 卡片图片发送后约 30 秒自动删除临时文件
- 插件内置中文字体（`assets/fonts`），无系统字体环境也可渲染

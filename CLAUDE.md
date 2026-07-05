# CLAUDE.md

## 项目概述

Stock Robot — AI 驱动的股票分析研报助手。输入 A 股代码，输出多维度分析报告。

## 语言规范

- 代码注释使用中文
- 文档（README、spec、plan 等）使用中文
- Git 提交信息使用中文，格式如下：

```
feat(功能点): 功能简述

详细描述（可选）
```

示例：
```
feat(数据层): 添加 AkShare 数据源适配器
feat(分析模块): 实现财务指标分析器
fix(缓存): 修复 SQLite 缓存过期判断逻辑错误
test(集成): 添加管道端到端集成测试
chore(项目): 初始化项目脚手架
```

## 技术栈

- Python 3.11+
- Pydantic v2, click, rich, AkShare, pandas, Jinja2, pytest
- openai SDK + anthropic SDK
- SQLite 缓存

## 架构

模块化管道：`CLI → 数据层 → 分析层 → LLM 层 → 报告构建 → 输出`

各层通过抽象基类解耦，数据采集和指标计算由确定性代码完成，LLM 仅在最后一步生成自然语言解读。

## 项目结构

```
stock_robot/
├── cli.py                     # CLI 入口
├── src/
│   ├── core/                  # 管道调度器、注册机制
│   ├── data/                  # 数据源抽象、Schema、缓存
│   ├── analysis/              # 分析模块（财务、技术、估值、行业、舆情）
│   ├── llm/                   # LLM 后端适配器、提示词模板
│   ├── report/                # 报告构建器
│   └── utils/                 # 配置管理、股票代码工具
├── tests/
├── docs/superpowers/          # 设计文档和实现计划
├── pyproject.toml
└── README.md
```

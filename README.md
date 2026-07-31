# Yogurt Chat Bot

酸奶是一个运行于 OpenClaw、Ollama 与微信官方 Bot 之上的本地 AI 角色。

本项目用于同步：

- 酸奶的人格与行为规则
- OpenClaw Workspace 文件
- 用户关系与共同经历记忆
- 回复风格路由
- 表达库与表情包系统
- 从 MaiBot 设计中移植的拟人化功能

## 架构

微信官方 Bot
    ↓
OpenClaw
    ↓
Yogurt Social Layer
    ├── 场景识别
    ├── 回复风格路由
    ├── 人物关系记忆
    ├── 共同经历检索
    ├── 表达库
    ├── 表情包选择
    └── 后台学习
    ↓
Ollama / Qwen

## 项目目录

- workspace/：OpenClaw 人格与规则文件
- social_layer/src/：社交人格层代码
- social_layer/config/：人格与行为配置
- social_layer/data/：本地运行数据
- docs/：架构和开发文档
- scripts/：同步和部署脚本

## 安全说明

不得提交：

- API Key、Token 或密码
- 微信登录凭据
- OpenClaw 主配置文件
- 会话数据库
- 日志
- 未脱敏的运行数据

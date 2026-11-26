# jcheng-applications

> 状态：**实验性**，2026-11-26。版本 0.1.0。
> 准入骨架加离线任务编排核心：配方校验、确定性规划，以及面向抽象网关端口的
> 失败关闭运行器。没有 HTTP/WS 客户端、没有运行进程，也不接触机器人。
> 运行中的机器人不会使用其中任何内容。
> 本文为英文主文档的中文对照；如有歧义，以 [README.md](README.md) 为准。

`jcheng-applications` 是 JCHENG 生态的 L4 任务编排应用。它把操作员编写的配方
（recipe）转换为确定性的意图请求序列，并交给 `jcheng-system` 网关评估。它不是
操作台——操作台是 [`jcheng-studio`](https://github.com/JCHENG-Robotics/jcheng-studio)——
也不是运动权威。控制租约、deadman、限幅、停止锁存与唯一运动入口都属于系统层；
应用只逐步提出请求，并原样报告网关的回答。

## 范围与边界

| 项 | 本仓库 |
|---|---|
| 层 | L4 任务编排（见[仓库分类](https://github.com/JCHENG-Robotics/.github/blob/main/docs/repo-taxonomy.md)） |
| 运行位置 | 操作员笔记本或工作站；绝不在机器人上运行 |
| 输入 | 配方 YAML、显式的每会话 `Authorization` 记录，以及网关的 `ContractInfo` / `SafetySnapshot` / `MotionResult`（M2/M3） |
| 输出 | 带稳定幂等键的确定性 `Plan`，以及内存中的 `RunReport`；M3 起产生网关意图请求；M3 起持久化运行记录 |
| 运动授权 | 无。控制租约、deadman、限幅、停止锁存与运动执行都属于系统层 |
| 硬件访问 | `none` |
| 拥有的设备 | 无 |
| 禁止 | 加入 DDS 域 0/41/42、`rclpy` 或 `ros2` CLI、设备访问、厂商话题、直接执行运动、`/cmd_vel`、SSH 登录机器人、把令牌放进 git、伪造或插值状态、对结果未知的运动自动重试、无人值守的硬件运行 |

权威划分是结构性的，不是编码约定：

- 配方是对意图序列的请求，不是可直接访问设备的程序。
- 运行器每步最多提交一个意图，绝不重试。超时、结果未知，或完成但缺少总线实测，
  都会停止本次运行。
- 运行需要该会话显式批准、且覆盖所需作用域的授权记录。上一个会话的授权不会延续。
- 系统层可以在任意时刻拒绝、中止、抢占或停止。应用原样报告该结果。
- 应用不是急停。平台没有急停、制动、编码器或自碰撞模型；系统层的停止锁存是
  软件停止。

## 目录

```text
jcheng-applications/
├── capability.yaml                 # 准入清单（offline，hardware_access: none）
├── docs/
│   ├── ARCHITECTURE.md             # 平面、组件与运行状态机
│   ├── INTEGRATION.md              # 网关契约、准入与失败语义
│   └── RECIPES.md                  # 配方格式、步骤、幂等与授权
├── recipes/
│   └── example-supervised-transfer.yaml  # 离线示例；不是硬件程序
├── src/jcheng_applications/
│   ├── model.py                    # 配方模型、契约常量与规划器
│   ├── gateway.py                  # GatewayPort 协议与契约解析
│   ├── runner.py                   # 失败关闭运行器与运行报告
│   └── cli.py                      # 离线 validate/plan 命令行
├── tests/                          # 契约、配方、网关、运行器与 CLI 测试
├── scripts/check_repo_gate.sh      # 仓库门禁（本地与 CI）
├── evidence/20261126_scaffold/     # M0/M1 证据记录
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## 依赖

- Python 3.10 或更新版本（CI 使用 3.12）、`PyYAML` 6.x、`pytest` 7.4+。
- 不需要 ROS，任何阶段都不需要：本仓库不加入 DDS 图，也不使用 RMW。网关是
  HTTP/WS 边界（M2）。
- 门禁、测试与 CLI 不需要机器人、网关或网络监听。
- 未来的任何执行（M2/M3）都需要 `jcheng-system` 网关和经操作员授权的会话；
  v0.1.0 都不使用。

## 快速开始

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'

# 校验随仓库提供的离线示例。
jcheng-applications validate recipes/example-supervised-transfer.yaml

# 打印确定性计划。不会联系网关。
jcheng-applications plan recipes/example-supervised-transfer.yaml \
  --run-id demo-001 --json

# 与 CI 等价。
python3 -m pytest -q
bash scripts/check_repo_gate.sh
```

v0.1.0 **刻意没有 `run` 命令**。网关传输属于 M2；在没有经过契约校验的网关时
提供一个看似可运行的命令，会诱发本仓库无法支撑的硬件声明。运行器只在测试中
针对进程内假网关进行验证。

## 验证状态

| 声明 | 状态 | 证据 |
|---|---|---|
| 仓库门禁、文档集与单元测试通过 | 已验证（L0/L1，2026-11-26） | `evidence/20261126_scaffold/`；`scripts/check_repo_gate.sh` |
| 配方按固定契约、作用域与各 kind 载荷规则校验 | 已验证（L1，2026-11-26） | `tests/test_recipe.py` |
| 计划确定，幂等键与运行绑定 | 已验证（L1，2026-11-26） | `tests/test_recipe.py`；`src/jcheng_applications/model.py` |
| 运行器在提交任何内容前拒绝缺失授权、作用域不足、契约不匹配、非 `ARMED_IDLE` 安全状态与缺失租约 | 已验证（L2，2026-11-26） | `tests/test_runner.py` |
| 被拒绝、中止、超时或未实测的步骤会停止运行，且绝不重试 | 已验证（L2，2026-11-26） | `tests/test_runner.py` |
| 仓库中不存在硬件、DDS、网络客户端或运动执行路径 | 已验证（L0，2026-11-26） | `tests/test_no_hardware.py` |
| HTTP/WS 网关客户端与实时契约检查 | 未验证 | 尚未实现（M2） |
| 向真实网关提交意图 | 未验证 | 尚未实现（M3） |
| 持久化运行记录与操作员会话关联 | 未验证 | 尚未实现（M2） |
| 任何硬件行为 | 未验证 | 无 L5 会话；无运行时 |

完整报告：[ACCEPTANCE.md](ACCEPTANCE.md)。

## 相关仓库

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — 网关 OpenAPI、WS 信封、消息 Schema 与契约哈希。
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — 特权运行时；网关所有者、租约持有者，也是运动的唯一来源。
- [`jcheng-studio`](https://github.com/JCHENG-Robotics/jcheng-studio) — L4 操作台；授权与观察会话的人机界面。
- [`jcheng-perception`](https://github.com/JCHENG-Robotics/jcheng-perception) — 建议感知目标，经网关呈现。
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — 无人值守健康与看门狗策略，由该仓库拥有。
- [`.github`](https://github.com/JCHENG-Robotics/.github) — 生态路线图、变更日志、验收与仓库分类。

## 许可证

第三方与版权审查尚未完成；尚未添加 `LICENSE` 文件。见
[LICENSE-REVIEW.md](LICENSE-REVIEW.md)。

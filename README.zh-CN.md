# jcheng-applications

> 状态：**实验性**，2026-11-26。版本 0.1.0。
> 准入骨架加离线任务编排核心：配方校验、确定性规划，以及面向抽象网关端口的
> 失败关闭运行器。M2 增加了一个只读传输（仅 GET、有界超时、不重试），
> 不提交意图、没有运行进程。M3 因系统侧 C2 探针失败而暂停。
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
│   ├── RECIPES.md                  # 配方格式、步骤、幂等与授权
│   ├── TASK_ENGINE.md              # 设计：树执行器、监督器与策略选择
│   └── M1_5_VALIDATION.md          # py_trees 隔离技术验证报告
├── recipes/
│   └── example-supervised-transfer.yaml  # 离线示例；不是硬件程序
├── src/jcheng_applications/
│   ├── model.py                    # 配方模型、契约常量与规划器
│   ├── gateway.py                  # GatewayPort 协议与契约解析
│   ├── transport.py                # M2 只读 GET 传输（不提交意图）
│   ├── runner.py                   # 失败关闭运行器与运行报告
│   └── cli.py                      # validate/plan 与只读 check 命令行
├── tests/                          # 契约、配方、网关、运行器与 CLI 测试
├── scripts/check_repo_gate.sh      # 仓库门禁（本地与 CI）
├── scripts/m1_5_pytrees_probe.py   # 候选运行时隔离探针（不是运行时依赖）
├── evidence/20261126_scaffold/     # M0/M1 证据记录
├── evidence/20261126_m1_5_pytrees/ # M1.5 隔离探针证据
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## 依赖

- Python 3.10 或更新版本（CI 使用 3.12）、`PyYAML` 6.x、`pytest` 7.4+。
- `py_trees` 2.6（BSD-3）是 M1.5 的候选树运行时，尚待许可证审查。v0.1.0
  不导入它，它目前也不是依赖。
- 不需要 ROS，任何阶段都不需要：本仓库不加入 DDS 图，也不使用 RMW。网关是
  HTTP/WS 边界。
- M2 传输使用标准库 `http.client`，不引入第三方 HTTP 依赖；仅 GET，且有界超时。
- 门禁不发起网络请求。传输测试绑定临时回环端口，不需要机器人、网关或网络监听。
- 任何执行（M3）都需要 `jcheng-system` 网关和经操作员授权的会话；M3 已暂停，
  M2 的 `check` 只读。

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

# M2 只读准入：仅 GET，不产生运动。令牌只从环境变量读取，不放在命令行。
export JCHENG_GATEWAY_URL=http://127.0.0.1:8766
export JCHENG_GATEWAY_TOKEN=...   # 由操作员设置；不得提交
jcheng-applications check --url "$JCHENG_GATEWAY_URL"

# 与 CI 等价。
python3 -m pytest -q
bash scripts/check_repo_gate.sh
```

**刻意没有 `run` 命令**。`check` 只读健康、契约、状态与安全，不做其他事情。
在 M2/M3 网关路径和 C2 结论之前提供看似可运行的命令，会诱发本仓库无法支撑的
硬件声明。运行器只在测试中针对进程内假网关进行验证。

## 验证状态

| 声明 | 状态 | 证据 |
|---|---|---|
| 仓库门禁、文档集与单元测试通过 | 已验证（L0/L1，2026-11-26） | `evidence/20261126_scaffold/`；`scripts/check_repo_gate.sh` |
| 配方按固定契约、作用域与各 kind 载荷规则校验 | 已验证（L1，2026-11-26） | `tests/test_recipe.py` |
| 计划确定，幂等键与运行绑定 | 已验证（L1，2026-11-26） | `tests/test_recipe.py`；`src/jcheng_applications/model.py` |
| 运行器在提交任何内容前拒绝缺失授权、作用域不足、契约不匹配、非 `ARMED_IDLE` 安全状态与缺失租约 | 已验证（L2，2026-11-26） | `tests/test_runner.py` |
| 被拒绝、中止、超时或未实测的步骤会停止运行，且绝不重试 | 已验证（L2，2026-11-26） | `tests/test_runner.py` |
| 仓库中不存在硬件、DDS 或运动执行路径；网络访问只允许 GET-only 的 `transport.py` | 已验证（L0，2026-11-26） | `tests/test_no_hardware.py` |
| 只读网关准入：四项 GET、有界超时、不重试、拒绝契约/鉴权/格式错误 | 部分验证（L2，2026-11-26） | `tests/test_transport.py`；仅回环 mock；CI 待跑 |
| 向真实网关提交意图 | 未验证；M3 暂停 | `jcheng-system` C2 失败 |
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

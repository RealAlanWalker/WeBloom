# AdventureX：从这里开始

## 当前已经完成

- 旧 Codex 任务 `019f8de8-e4eb-7c32-87d9-b8233087eea3` 的工作目录已经迁移到 `E:\AdventureX`。
- 交付主目录：`E:\AdventureX\outputs\ADX_TouchDesigner_OSC_Bridge`。
- Windows 演示入口：`E:\AdventureX\outputs\ADX_TouchDesigner_OSC_Bridge\run_demo_windows.bat`。
- Windows 实时 CSV 入口：`E:\AdventureX\outputs\ADX_TouchDesigner_OSC_Bridge\run_live_csv_windows.bat`。
- 交付说明：`E:\AdventureX\outputs\ADX_TouchDesigner_OSC_Bridge\README_交付说明.md`。
- 38 个原始文件的 SHA-256 与旧目录全部一致。
- 单元测试 7/7 通过；466 行样本回放的本机处理 p95 约 0.168 ms。
- TouchDesigner 2023.12370 正在运行；其内置 Python 是 3.11.1。
- 花朵工程、MCP、OSC 9000 和实时 CSV 尾随均已打通；最终输出为 `/project1/adx_final_out`。

当前尚未处理 TD 到网页/App 的 WebRTC 发布层。

## 当前实时 CSV 用法

1. TouchDesigner 打开：`E:\AdventureX\touchdesigner\ADX_Flower_mcp_test.toe`。
2. 双击 `run_live_csv_windows.bat`，或运行 `live_csv.ps1 start`。
3. 硬件网关持续追加：`E:\AdventureX\outputs\ADX_TouchDesigner_OSC_Bridge\live\live_sensor.csv`。
4. CSV 每完成一行写入，就会实时更新 TD 中的 `growth`、`bloom`、`glow`、`pulse`、`motion`、`sparkle` 等通道。

这条链路已经通过实际追加 120 行 CSV 验证，TD 工程节点错误为 0。当前映射为：

| 信号 | 第一版只控制什么 |
|---|---|
| `growth` | 生长进度、花瓣层数 |
| `bloom` | 花瓣开合角度 |
| `glow` | 发光强度 |
| `pulse` | 轻微呼吸缩放 |
| `motion` | 扰动和抖动 |
| `sway_x/y` | 花茎摆动 |
| `twist` | 花冠旋转 |
| `mist` | 雾和低速粒子 |
| `sparkle` | 瞬时粒子爆发 |
| `result_ready` | 锁定最终花并进入结果页 |

## 硬件联调的下一步

演示样本跑通后，让网关同事选择一种输入：

- 推荐：每约 100 ms 向本机 UDP `9100` 发送一条同字段名 JSON；运行 `bridge.py udp-json --listen-host 0.0.0.0 --listen-port 9100`。
- 当前已运行：网关持续追加 CSV；后台执行 `bridge.py tail --csv <实时文件>`。

正式双设备联调时，把 `config.json` 中的 `single_device_fallback` 改为 `false`。不要把 LLM 放进实时热路径；AI 负责搭节点、调映射和排错，实时数据始终走本地确定性处理。

## AI 控制 TouchDesigner：可行，但先在副本测试

### 当前 MCP 安装状态

- 已安装项目本地 `td-mcp 0.1.3`：`E:\AdventureX\integrations\td-mcp`。
- 已生成 TD 回调：`E:\AdventureX\touchdesigner\td_mcp_callbacks.py`。
- 已写入项目 Codex 配置：`E:\AdventureX\.codex\config.toml`。
- MCP stdio 初始化通过，已发现 12 个 TouchDesigner 工具。
- 回调已通过 TouchDesigner 自带 Python 3.11 的语法检查。
- TD 中的 `Web Server DAT` 已创建并启用，MCP 可直接读取、修改和截图验证当前工程。

TouchDesigner 2023.12370 没有已确认的官方内置 MCP。社区方案通常通过 `Web Server DAT + TouchDesigner Python` 暴露节点创建、连线、参数设置、错误检查和预览，再由 Codex 作为 MCP 客户端调用。

当前工程建议按这个顺序：

1. 先用现有 OSC 包完成稳定演示，它不依赖 MCP。
2. 复制工程为 `ADX_Flower_mcp_test.toe`，只在副本里安装 MCP。
3. MCP 只监听 `127.0.0.1`，不要暴露到公网或公共 Wi-Fi。
4. 先让 AI 做无风险任务：列出节点、创建一个 Noise TOP、连接 Null TOP、截图验证。
5. 验证保存/重开正常后，再让 AI 修改正式工程。

针对当前旧版 TD，优先做兼容性小测，不直接安装要求 TD 2025 的工具。较新的 Twozero/Embody 明确面向 2025 构建，不适合直接装进 2023.12370。社区 `touchdesigner-mcp`、`tdmcp` 和轻量 `td-mcp` 都能接 Codex，但公开文档没有明确保证 2023.12370；因此必须先在副本验证 `.tox`/回调脚本是否能载入。

## 网页/App 暂时不要先做

比赛单屏版本先用 TD 的 Perform Mode 全屏，风险最低。等花朵交互完成后，再增加：

`TD final_render → Video Stream Out TOP → WebRTC → 域名网页/App`

这会新增信令服务、STUN/TURN、断流重连和浏览器接收页，不应阻塞当前 OSC 与花朵映射。

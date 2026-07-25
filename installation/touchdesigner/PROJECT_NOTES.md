# ADX 双人情绪花：完整生命周期实时点云

当前主体直接迁入下面 TOX 里的完整五株点云，而不是单个 PLY 或随机拼花：

```text
E:\AdventureX\outputs\ADX_Flower_Cluster\迁移包\adx_flower.tox
```

成品保留三株 `flower2.ply` 与两株 `sutsuki.ply`，每株拥有独立的点云变形、颜色与 Geometry 管线，共用同一组实时生命周期信号。

## 当前运行状态

- 成品工程：`ADX_Flower_PointCloud.toe`
- 默认模式：实时 CSV 开启，Demo 关闭
- 实时输入：`live\sensor_live.csv`
- T5 音频支链（检测到 SERIAL-B 时）：`live\audio\`，包含 WAV 与 `audio_events.csv`
- 纯画面输出：`1080 × 1920`，只有花与黑色背景
- 当前五株预览：<http://127.0.0.1:9991/>（9987 已被另一份预览占用）
- TouchDesigner 主体：`/project1/adx_flower_migrated/adx_flower`
- TouchDesigner 输出节点：`/project1/adx_cluster_source`、`/project1/adx_final_out`
- 无边框输出窗口：`/project1/adx_show`（按需手动打开 `Open as Separate Window`）

网页已经由工程内的 Web Server DAT 提供，打开后没有 TouchDesigner 按钮或操作界面；点击画面可请求浏览器全屏。

## 完整生命周期

实时 CSV 持续增加新行时，`/project1/adx_flower_migrated/adx_flower/ctrl/signal` 尾随文件。生命周期不再把信号映射到 `0.22/0.44/0.70/0.88` 等固定目标；双人佩戴提示、有效 BPM、距离、心率同步与 IMU 共同组成连续的 `signal_strength`，它实时决定生长速度。阶段编号只用于显示，绝不控制进度跳转。

两人的低灌注提示配合真实距离或动作即可开始，不要求两边每一帧都有 BPM；信号越丰富越接近约一分钟完成，信号暂时不足时暂停在当前位置而不倒退。CSV 超过 8 秒没有更新时立即判为离线，不复用旧行继续生长。

盛放完成后不再周期性自动扩散。任一有效 IMU 检测到约 `0.32 g` 的瞬时重力偏差，或归一化陀螺仪强度超过 `0.72`，会触发一次快速扩散、短暂停留和约 3 秒平滑收敛；恢复安静后才重新武装下一次触发。

主要状态通道位于 `/project1/adx_flower_migrated/adx_flower/ctrl/OUT_CTRL`：`progress`、`growth`、`bloom`、`openness`、`signal_strength`、`motion`、`accel_anomaly`、`motion_burst` 与 `disperse`。

## 运行方式

### 使用真实网关与两块传感节点

确认 S3 网关和两块 C3 传感节点已上电，然后双击：

```text
3_启动真实硬件花丛.bat
```

启动器会按硬件 README 中登记的 USB 序列号自动识别 S3 网关，停止旧的示例回放，启动 `hardware\desktop\collector.py --live-bpm`，并把 138 列原始宽表直接写入 `live\sensor_live.csv`。每次启动会发送新会话复位令牌，使花从 0 开始；TouchDesigner 不会被带到前台。两个设备需要同时提供最低限度的佩戴/距离/动作证据，随后 BPM、靠近程度、同步与动作连续调节生长速度。

正常状态使用清晰、稳定的粒子点云，不再叠加模糊辉光或逐点往复震荡。任意一块有效 IMU 检测到快速大幅挥手时，会触发一次明显的大范围散开，并在约 3 秒内自动收敛回距离定义的正常点云；另一块板可以暂时没有 IMU。

当前实物枚举为 S3 网关 `COM7`，T5 为 `COM5/COM6`。启动器会自动识别 T5 的 CH342 `SERIAL-B` 接口；检测到后，T5 在同一互动区进入/退出事件上自动开始/停止录音，并把 WAV 与 `audio_events.csv` 写入 `live\audio\`。没有 T5 时仅跳过音频支链，不影响 CSV、BPM 和花丛。若 CSV 只有表头，应在采集器启动后复位或重新上电两块 C3 节点，这与 `hardware\README.md` 规定的启动顺序一致。

### 回放你给的示例 CSV

当前已在后台运行并循环回放。以后可双击：

```text
2_回放示例CSV.bat
```

它读取：

```text
D:\常用软件\xwechat_files\wxid_p06lmvotchxu12_27c6\msg\file\2026-07\sensor_data_20260725_003324.csv
```

并持续写入 `live\sensor_live.csv`，保留原始行间节奏并自动循环。

### 使用模拟信号

双击 `1_启动信号源.bat`。脚本会连续模拟两个人的情绪/距离数据并写入同一个实时 CSV。

### 接真实传感器

让传感器程序以相同表头持续追加到：

```text
E:\AdventureX\outputs\ADX_Flower_PointCloud\live\sensor_live.csv
```

TouchDesigner 无需重启，会自动尾随新行。

## 已验证

- 冷启动重新打开 `.toe` 成功，生命周期状态从 CSV 重新驱动。
- 五组点云均加载：三组 `565 × 565`、两组 `736 × 736`。
- 五个 GLSL、五个 Geometry、Render、Web Server、Window 均无 error / warning。
- 网页首页返回 HTML，`/frame.jpg` 返回实时 JPEG。
- 加速演示实测进度 `0 → 0.195 → 0.566 → 1.0`，没有固定目标跳转。
- IMU 演示实测扩散 `0 → 1.0 → 0.672 → 0`，收敛后精确恢复完整五株花型。

## 交付文件

| 文件 | 内容 |
|---|---|
| `ADX_Flower_PointCloud.toe` | 可直接打开的最终工程 |
| `demo_live_flow_20260726\contact_sheet.jpg` | 连续生长、IMU 扩散和恢复视觉证据 |
| `preview_bloom.png` | 完整盛放静帧 |
| `sample_replay_stage_00_zero.png`～`05.png` | 示例 CSV 实际驱动阶段证据 |
| `1_启动信号源.bat` | 模拟信号入口 |
| `2_回放示例CSV.bat` | 用户示例 CSV 循环回放入口 |

迁移与实时逻辑脚本：

```text
E:\AdventureX\touchdesigner\install_migrated_cluster.py
E:\AdventureX\touchdesigner\adx_live_signal_callbacks.py
```

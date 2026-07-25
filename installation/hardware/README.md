# SynBloom 硬件传感采集子系统

本仓库只包含 SynBloom 整体项目中的一部分，主要是可穿戴设备的硬件传感采集实现，
不代表完整的 SynBloom 产品、应用或交互系统。

当前实现包括两个 ESP32-C3 传感节点、一个 ESP32-S3 无线网关，以及配套的桌面采集和
验证工具。节点采集 PPG 和可选 IMU，通过 ESP-NOW 向网关发送近似原始数据，同时利用
对端包的接收 RSSI 提供实验性的粗略距离、互动区和接近/远离趋势。Python 采集器保存
单一宽表 CSV，并提供不修改原始数据的实时 BPM 旁路。T5AI-Core 音频节点通过 USB
直连电脑。

## 仓库范围

本仓库负责：

- ESP32-C3 传感节点固件；
- ESP32-S3 数据网关固件；
- MAX30102、MPU6050 和 ESP-NOW RSSI 的采集与传输；
- 电脑端原始数据记录、实时 BPM 旁路和实验性标定工具；
- 与上述硬件链路直接相关的诊断程序和自动回归测试。

本仓库不包含 SynBloom 的完整产品设计、上层应用、最终交互体验、服务端或其他团队模块。
这里的目录结构和说明仅描述当前硬件采集子系统。

音频链路与传感链路分离：电脑从 S3 收到互动区状态后控制 T5，T5 通过独立 USB
串口发送 PCM。PCM 不经过 C3、ESP-NOW、S3 网关或现有 138 列 CSV。

该子系统用于原型采集和算法验证，不是医疗设备，实时 BPM 和 RSSI 距离都不能用于医疗
诊断或安全关键决策。

当前链路：

```text
MAX30102 + 可选 MPU6050
              |
              v
        ESP32-C3 SuperMini <--- ESP-NOW RSSI --> 另一块 ESP32-C3
              |
              | ESP-NOW channel 1
              v
        ESP32-S3 gateway
              |
              | XVP2 binary serial
              v
        Python collector
          |           |
          v           v
          单一 PPG/IMU/RSSI 宽表 CSV
```

## 1. 接线

接线和移动杜邦线前必须断开 USB。上电前用万用表检查 `3V3` 与 `GND`
没有短路。

| ESP32-C3 SuperMini | MAX30102 | MPU6050/GY-521 |
|---|---|---|
| `3V3` | `VCC` | `VCC` |
| `GND` | `GND` | `GND` |
| `GPIO8` | `SDA` | `SDA` |
| `GPIO9` | `SCL` | `SCL` |

- 超声波模块已经从正式方案移除，GPIO5/GPIO6 不再使用。
- MAX30102 的 `INT` 不接。
- MPU6050 的 `INT`、`AD0`、`XDA`、`XCL` 不接。
- 当前 COM9 实物扫描仅在 GPIO8/GPIO9 上发现 `0x57` 和 `0x68`，因此正式程序
  使用这组已经实测的 I2C 引脚；板载 LED 不再作为状态灯使用。

## 2. 目录

```text
synbloom/
  firmware/
    multisensor_node/       ESP32-C3 采集节点
    multisensor_gateway/    ESP32-S3 网关
    t5_audio_node/          T5AI-Core USB 实时 PCM 固件（复制到 TuyaOpen apps 构建）
    espnow_rssi_power_sweep_diagnostic/  双向功率扫描
    espnow_rssi_channel_power_diagnostic/ 信道与功率上限诊断
  desktop/
    collector.py            串口解析、单一宽表 CSV、容错实时 BPM、UDP
    heart_rate.py           可复用的按节点心率分析器
    test_collector.py       协议、解析和输出测试
    capture_espnow_calibration.py  固定距离、预热隔离的双向 RSSI 采集
    fit_espnow_ranging.py   至少四距离、双接收端 RSSI 模型拟合
    ranging_trend.py        历史 CSV 接近/远离算法回放
    test_heart_rate.py      合成 PPG 与缺口容错测试
    analyze_ppg_profiles.py 光学配置扫描评估
    requirements.txt
  data/                       本地运行输出目录（仓库仅保留空目录）
```

两个固件目录各带一份相同的 `telemetry_packet.h`。修改协议时必须同步修改两份，
并运行电脑端测试及两次固件编译。

## 3. 固件行为

- MAX30102：原始 `400 Hz`，FIFO 平均 `4`，输出约 `100 Hz` Red/IR；每包
  `10` 点。
- MPU6050：正负 `2g`、正负 `250 dps`、DLPF 配置 `3`；读取频率 `10 Hz`；
  每包 `5` 点。
- 两块 C3 从对方已有的约 `10 Hz` PPG ESP-NOW 广播帧读取接收 RSSI，不增加无线包，
  也不再启动 BLE。只接受登记的两块节点 MAC 和匹配的设备 ID。
- RSSI 使用 `11` 点中位数和 `alpha=0.20` 的指数平滑。两个相邻的 `1.5 s` 窗口
  计算平均 RSSI 差；连续 `3` 次达到 `+3/-3 dB` 分别判定 approaching/receding，
  回到 `±1 dB` 内则判定 stable。该趋势完全在节点本地计算，不依赖网关。
- 节点仍保留兼容的粗略距离和本地状态字段；电脑端使用两个方向的滤波 RSSI执行最终
  互动区判定。
- `RANGING_CONFIG_VERSION=2` 来自两块实板在 `0.5/1.0/1.5/2.0/3.0 m` 的双向
  `60 s` 实测。person_01 使用 `A=-85.699, n=1.6084`，person_40 使用
  `A=-85.125, n=2.0473`。0.5–3.0 m 外仍输出外推值，但明确标记
  `distance_extrapolated=1`。

五点原始 RSSI 中位数（括号内为标准差，单位 dB）：

| 距离 | person_01 | person_40 |
|---:|---:|---:|
| 0.5 m | -81 (2.36) | -79 (2.37) |
| 1.0 m | -86 (2.70) | -86 (3.23) |
| 1.5 m | -88 (2.89) | -88 (4.06) |
| 2.0 m | -90 (1.79) | -90 (1.83) |
| 3.0 m | -94 (2.17) | -96 (1.61) |

固件中的 RSSI 距离参数来自本地实测；原始采集文件不提交到仓库。距离结果只用于调试，
互动区最终判定直接使用电脑端 RSSI 迟滞。
- 两节点使用网关同步时钟划分不同的 100 ms ESP-NOW 发送时隙，降低周期广播碰撞。
- PPG 和上下文分别进入有界发送队列；PPG优先发送，队列拥堵时允许明确丢弃旧包，
  但 MAX30102 FIFO 必须持续排空。
- 节点通过网关同步帧修正时间；同步失效超过 `5 s` 时继续用本地时钟。
- 网关通过 ESP32-S3 的硬件 CDC 输出到原生 USB Serial/JTAG COM 口；当前
  ESP32 Core 中 `Serial0` 是 UART0，不能用于这条 USB 采集链路。
- MPU6050属于可选新增输入：未识别到 `0x68` 时上下文包会标记 IMU无效，
  但不能阻止 MAX30102 PPG继续采集和发送。

阈值集中在 `multisensor_node.ino` 顶部，可根据结构调整：

```cpp
constexpr uint16_t ZONE_ENTER_MM = 1200;
constexpr uint16_t ZONE_EXIT_MM = 1500;
constexpr uint8_t ZONE_ENTER_CONFIRMATIONS = 3;
constexpr uint8_t ZONE_EXIT_CONFIRMATIONS = 3;
constexpr int8_t ESPNOW_TX_POWER_QDBM = 80; // 20 dBm，单位为 0.25 dBm
constexpr uint16_t RANGING_CONFIG_VERSION = 2; // 0.5–3.0 m 五点实测
```

## 4. 环境和编译

已验证环境：

```text
Arduino CLI 1.5.1
ESP32 Arduino Core 3.3.10
SparkFun MAX3010x Library 1.1.2
Python 3.14
```

ESP32-C3 节点：

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32c3 firmware\multisensor_node
arduino-cli upload --fqbn esp32:esp32:esp32c3 --port COM_NODE firmware\multisensor_node
```

带 CH343 USB 串口芯片的 C3 SuperMini 使用 `ESP32C3 Dev Module`，保持
`USB CDC On Boot` 关闭。若实物使用 C3 原生 USB，应按实际板型启用对应选项。

ESP32-S3 网关使用项目已验证的 N16R8 编译配置：

```powershell
arduino-cli compile --fqbn "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi" firmware\multisensor_gateway
arduino-cli upload --fqbn "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi" --port COM_GATEWAY firmware\multisensor_gateway
```

`COM_NODE` 和 `COM_GATEWAY` 必须替换成 `arduino-cli board list` 当前显示的端口。

## 5. 电脑采集

安装依赖并运行测试：

```powershell
python -m pip install -r desktop\requirements.txt
python -m unittest discover -s desktop -p "test_*.py" -v
```

完整采集只需一条命令：

```powershell
python desktop\collector.py --full
```

`--full` 会按实物 USB 序列号自动识别 S3 网关，按 CH342 `SERIAL-B` 接口识别 T5，
同时保存传感宽表、显示实时 BPM、监听互动区并保存 WAV。在 AdventureX 完整工程中，
未显式传入 `--output` 时，`--full` 默认直接写入
`outputs\ADX_Flower_PointCloud\live\sensor_live.csv`，即 TouchDesigner 和网页正在读取的
实时文件；可继续用 `--output PATH` 覆盖。T5 音频是独立支链，COM5 缺失或被占用时会
输出警告，但不再阻止 COM7 上的 PPG/IMU/距离 CSV 采集。运行前仍应使用
`arduino-cli board list` 确认所有设备已枚举；不要再打开占用 COM11 或 COM7 的串口监视器。

互动区最终判定在电脑端完成，不依赖固件中的距离或 `zone_state`。不做现场校准时沿用
当前经验条件：两个方向均为 `-87 dBm` 进入、`-90 dBm` 退出。需要适配新场地时，只需
让两人佩戴节点并站在希望采用的互动区分界线上，保持正常朝向和姿态，运行：

```powershell
python desktop\collector.py --calibrate-interaction-zone
```

程序先等待 `8 s`，再采集边界 RSSI `30 s`，过滤离群值并分别估计两个接收方向的边界
能量，然后参考当前实测经验在边界两侧设置总宽 `3 dB` 的进入/退出迟滞。结果保存为
`interaction_configs/001.json`、`interaction_configs/002.json` 这样的递增编号文件，
校验后程序直接用本次结果进入正式采集。
现场校准只简化了边界取样，不会简化最终判定：仍要求
连续 `3` 次确认、两个接收方向同时进入；进入后两个方向均退出才正常停止，避免单方向
短暂遮挡造成抖动，但任一方向超过失联时限仍立即停止。需要延长采集可指定秒数，例如
`--calibrate-interaction-zone 60`。可用 `--save-interaction-config PATH` 覆盖默认编号
文件名。普通 `--full` 不会自动加载任何历史校验，必须显式传入
`--interaction-config PATH`。直接运行 `--full` 始终使用上述当前默认条件，任一方向超过
`2.5 s` 无有效 RSSI 即退出。

配置文件可以保存在任意目录；例如：

```powershell
python desktop\collector.py --full `
  --interaction-config interaction_configs\001.json
```

只有显式指定的配置会被加载。现场配置和采集结果属于本地运行数据，不提交到仓库。

启动顺序很重要：

1. 连接并启动 ESP32-S3 网关。
2. 先启动电脑采集器。
3. 再复位或上电 C3 节点。

直接在同一终端显示实时 BPM：

```powershell
python desktop\collector.py --port COM_GATEWAY --live-bpm
```

自动化实测可加 `--duration 60`，到时会刷新未配对缓存并正常关闭 CSV。

如本地软件还需要原始 UDP 数据，可同时启用 UDP；消息结构保持不变：

```powershell
python desktop\collector.py --port COM_GATEWAY --live-bpm `
  --udp 127.0.0.1:8765
```

每次启动只创建一个宽表文件：

```text
data/sensor_data_YYYYMMDD_HHMMSS.csv
```

一行对应约 100 ms 的采集周期，列按固定区块排列：

1. 第 1–4 列：行接收时间、节点、MAC和配对时间差。
2. 第 5–31 列：PPG包元数据，以及 `ppg_red_0..9`、`ppg_ir_0..9` 共10个原始点。
3. 第 32–39 列：上下文包和IMU元数据。
4. 第 40–69 列：5个IMU样本的六轴 `int16` 原值。
5. 第 70–99 列：同一批IMU值换算后的g和dps。
6. 第 100–118 列：距离、ESP-NOW RSSI、功率、配置版本、1 米状态和移动趋势。
7. 第 119–126 列：当前BPM及其旁路分析状态。
8. 第 127–130 列：PPG样本/包缺口与上下文缺口。
9. 第 131–138 列：PPG、上下文各自的网关时间戳和累计计数。

PPG 和上下文按同一节点的首样本时间戳配对，允许的时间差为 60 ms，并写入
`pair_delta_ms`。`ppg_present` 和 `context_present` 指明相应列块是否真实存在；缺包时
整组留空，不会插值或拿上一包冒充。没有 MPU6050 的节点在收到对端 RSSI 时仍会发送
`imu_valid=0` 的上下文；RSSI 也不可用时才可能得到 `context_present=0`。实时 BPM 会
重复附在后续宽行中，只有
`heart_rate_updated=1` 的行代表本次重新计算。

不启用 `--live-bpm` 时 BPM 列保持为空，但 PPG、IMU和距离仍写入同一个文件。
原有 UDP JSON 字段继续存在；context v3 新增趋势、1 米状态和外推标志，旧
`ble_rssi_dbm` 在新固件下固定为 `null`。

终端中的三个缺口指标含义不同：

- `ppg_packet_loss`：PPG 无线包序号缺口。
- `sample_missing`：由 PPG `sample_seq` 得出的真实样本缺失率，直接影响 BPM 质量。
- `context_gap`：IMU/距离上下文包序号缺口，不参与 BPM 连续性判断。

实时分析按节点维护完整的 8 秒 IR 窗口，在 40–200 BPM 全范围内检测单次脉搏峰，并以
至少 5 个可信峰的中位心搏间隔计算 BPM。形态滤波用于定位脉搏峰，零相位带通用于评价
自相关和频谱证据；两条路径的滤波边界都由配置的 BPM 范围推导，不偏向某个心率。
自相关和频谱只生成、评价周期候选，不直接把最高峰换算为心率。候选周期确定后，分析器
会在每个预期心搏位置的 ±18% 周期范围内重新搜索局部峰，因此弱心搏不必先通过全局
突出度门限；网格评分同时惩罚缺失心搏、额外峰和不稳定的心搏间隔。最终准入联合检查
心搏网格、自相关、频谱峰底比、频谱能量集中度，以及中位 IBI 与候选周期的一致性。
当最佳候选缺少自相关重复性且只解释少量心搏时，还会与所有能解释更多独立心搏的
候选竞争；只有网格、频谱和间隔证据相近的密集心搏序列才能取代稀疏慢波。强证据必须
获得自相关支持或在窗口内匹配至少 8 个心搏，避免把慢速压力/基线波动仅凭频谱集中度
标成 `good`。一致但较弱的证据输出 `degraded`。样本缺失 10%–20% 也会把有效结果
降为 `degraded`，超过 20% 则为 `too_many_gaps` 并停止显示 BPM。

为兼顾准确性和持续显示，极强的一致证据可以立即显示，普通弱证据首次出现时需要连续
3 个窗口相互一致；已有跟踪值后，相对变化超过 9% 的候选必须连续获得 3 个强证据窗口
才接管显示。历史 BPM 只作为候选比较和变化确认的内部参考；当前窗口证据不足时 BPM
仍然为空，不会沿用或伪造旧值。这可抑制持续慢波造成的逐秒“棘轮式”漂移，同时允许
真实心率变化在获得连续证据后更新。

单个不超过 500 ms 的样本缺口只会在内存分析窗口中线性插值。`no_contact` 表示 IR
低于贴肤阈值；初次贴上或一秒中位数突变超过 3% 后，需要连续 4 个一秒窗口的相邻
基线变化低于 0.5% 才退出 `contact_unstable`。锁定后，低于 3% 的缓慢漂移不会反复
触发重新预热；最近 250 ms 的快速检查会及时拦截尚未覆盖完整一秒的摘下或压力突变。
其他状态包括 `warming_up`、`long_gap`、`low_signal`、
`low_periodicity`、`restarted` 和 `sample_rate_changed`。

新增 UDP 消息类型为 `sensor_context_packet`。原 PPG 消息仍为
`raw_ppg_packet`，原分析器会忽略上下文消息。

也可以指定输出位置：

```powershell
python desktop\collector.py --port COM_GATEWAY `
  --output data\sensor_data.csv `
  --live-bpm
```

后处理时必须使用 `sample_seq` 和 `sample_timestamp_ms` 对齐 PPG 与上下文数据，
不能用 `received_at` 推断采样间隔。实时 BPM 仅用于原型验证，不是医疗测量。

### 数据保真边界

采集文件是主产物，实时 BPM 是只读旁路。宽表的 PPG 和 IMU 列直接来自节点包；
分析器产生的插值、去趋势结果只用于计算 BPM，不会写入 PPG/IMU 列，也不会筛除、
平滑或重采样原始值。BPM 和状态只占用独立的派生列。

需要区分“相对于无线包原样保存”和“传感器芯片最原始输出”：

- 宽表中的 `ppg_red_0..9`、`ppg_ir_0..9` 与无线包内的整数逐点一致，但节点已经把
  MAX30102 的 400 Hz FIFO 数据每 4 点平均为约 100 Hz，因此它是近似原始 PPG，
  不是未经平均的 400 Hz 芯片流。
- `imu_*_raw_0..4` 保留 MPU6050 六轴 `int16` 寄存器原值，同时提供换算后的 g/dps；
  后处理应优先保留原值列。
- `ranging_rssi_raw_dbm` 和 `ranging_rssi_filtered_dbm` 分别保存最新 ESP-NOW 接收值和
  实际参与模型计算的滤波值；`ranging_peer_device_id`、`ranging_tx_power_qdbm`、
  `ranging_config_version` 使数据可以复算。`distance_source` 为 `espnow_rssi`，旧
  `ble_rssi_dbm` 保留为空。
- `distance_mm` 是每个接收节点独立标定的对数路径损耗模型估计值，不是飞行时间测量。
  `within_one_meter` 是兼容保留的互动区字段，实际表示基于 RSSI 迟滞的互动状态；
  `range_trend_name` 独立描述接近、远离或稳定；超出当前配置实际采用的最小、最大
  标定距离时 `distance_extrapolated=1`。

## 6. ESP-NOW 功率扫描、标定与验收

1. 向两块 C3 烧录同一个 `espnow_rssi_power_sweep_diagnostic`。程序每 `30 s` 自动
   轮换 `8.5/13/17/20 dBm`，每条接收 JSON 都带发送端请求值、控制器回读值、方向和
   RSSI。固定在 `1 m` 完成一轮；排除重启、发送错误或生产固件 PPG 丢包超过 `5%`
   的档位，再选择双向较差一侧中位 RSSI 最强的档位，`2 dB` 内并列选较低功率。
2. 恢复正式节点和 v3 网关，保持高度、天线朝向和周围环境不变，自行量取至少四个
   不同距离，每个距离采集 `60 s`。距离应覆盖实际使用范围并尽量分散；命令中的
   `--distance` 填当次实测值，不要求使用预设整点。下列示例会保持串口打开并先丢弃
   `3 s` 预热数据：

```powershell
python desktop\capture_espnow_calibration.py --port COM7 `
  --distance 1.37 --duration 60 --warmup 3 `
  --output data\espnow_calibration_1p37m.csv
```
3. 拟合两个独立接收端配置：

```powershell
python desktop\fit_espnow_ranging.py `
  --sample <实测距离1>=data\espnow_calibration_<距离1>m.csv `
  --sample <实测距离2>=data\espnow_calibration_<距离2>m.csv `
  --sample <实测距离3>=data\espnow_calibration_<距离3>m.csv `
  --sample <实测距离4>=data\espnow_calibration_<距离4>m.csv `
  --config-version 3 --output data\espnow_ranging_config_v3.json
```

   可以追加任意数量的 `--sample`。工具按命令中的实际距离排序，并以最小、最大测点
   自动生成固件的有效标定范围。每个测点先用 Hampel 中位数/MAD 过滤离群 RSSI，再以
   保留样本中位数代表该点，最后用 Theil-Sen 稳健回归拟合。输出报告同时保留原始、
   保留和排除样本数；稳健处理只能降低 RSSI 干扰影响，不能修正量尺或参考点误差。
4. 用结果中的 `cpp_configuration` 整体替换节点源码从
   `BEGIN GENERATED RANGING CONFIG` 到 `END GENERATED RANGING CONFIG` 的完整区块，
   重新编译并同时烧录节点与 v3
   网关。节点启动行必须显示 `ranging_calibrated=true`、正数配置版本，以及功率请求值
   和回读值一致。
5. 验收：远区每端至少 `20` 个有效 RSSI 且有效率不低于 `80%`；互动区进入、退出需
   满足双向一致和迟滞确认；历史静止数据每端每分钟方向误触发不超过 `2` 次，已有移动记录在
   `3.5 s` 内识别方向。断开任一节点后另一节点 `2 s` 内失效。

若软件功率和标定均无法通过以上验收，应记录为当前硬件射频条件不达标，不能继续
调模型参数来伪造结果。MAX30102 数据和 RSSI 距离均为实验性输入，不用于医疗诊断。

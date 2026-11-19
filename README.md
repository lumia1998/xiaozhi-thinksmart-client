# ThinkSmart Client

运行在联想 ThinkSmart View（CD-18781Y）Ubuntu 带屏音箱上的语音客户端。通过本地唤醒词「小葵」发起对话，连接 [xiaozhi-hub-server](https://github.com/lumia1998/xiaozhi-hub-server) 完成语音识别、模型调用、视觉分析和语音合成。

客户端负责麦克风、扬声器、屏幕、摄像头采集及设备控制。界面由设备本机的 Web 服务提供，默认地址为 `http://127.0.0.1:8090/`。

首页的天气、智能家居和计划直接打开触屏面板，不发聊天消息。天气和家居需要支持 `/display/*` 的 Hub 和有效的设备配对令牌。家居仅显示 Hub 已授权查询的实体，控制权限和实际开关状态独立判断。计划按本地日期保存到音箱配置目录的 `agenda.db`，支持跨月查看、新增和删除，不需要联网，不会自动设置闹钟。

日程页左侧选日期，右侧按时间显示事项。按住底部麦克风说一句，例如“上午九点叫我起床”，松开后核对时间和事项，再保存到所选日期。语音录入需要设备配对及 Hub 支持 `recognize_pcm` 的本地 ASR，最长 10 秒，仅转写，不发起聊天或执行设备指令；离线可用“手动添加”。点事项可展开删除按钮。

## 功能

| 功能 | 当前实现 |
| --- | --- |
| 语音交互 | 本地唤醒、点按说话、按住说话、文字提问、唤醒词或按钮打断 |
| 音频处理 | PipeWire 音频输入输出、回声消除、Opus 流式传输 |
| 屏幕界面 | 仪表盘、对话页、可搜索的本地已载入聊天记录、模型选择和设备设置 |
| 多媒体 | 显示图片，点击播放视频和音频，提供进度与音量控件 |
| 摄像头视觉 | 模型按需调用设备拍照工具，上传单帧图片到 Hub 识别并播报 |
| 语音控制 | 绝对或相对调节音量、常规亮度，查询和修改定时亮度 |
| 定时亮度 | 每天指定时段使用设定亮度，结束后恢复常规亮度，支持跨午夜 |
| 自动熄屏 | 设置空闲熄屏时间，触摸、唤醒及对话时配合系统亮屏 |
| 蓝牙音箱 | 手机配对、连接管理，对话时降低背景音乐音量 |
| 服务器设置 | 配置 Hub HTTP/WebSocket 地址、检测连接、保存设备配对令牌 |

## 使用示例

- “小葵，今天有什么安排？”
- “音量调到 50”“声音小一点”
- “屏幕亮一点”“亮度调到 60”
- “每天零点到早上六点，亮度设为 30%”
- “用摄像头看看我手里是什么”“读一下前面的文字”

设备操作由 Hub 模型调用 MCP 工具执行。摄像头语音交互依赖可用的系统摄像头和 Hub 视觉模型。

## 运行环境

**Windows 界面预览**：安装 Python 3.10 或更高版本后，在仓库根目录运行 `powershell -ExecutionPolicy Bypass -File test/windows-preview.ps1`，打开 `http://127.0.0.1:18090/`。页面直接使用 `web/v2`，修改后刷新即可。可查看示例天气、切换模拟家电、试聊、按住语音按钮生成示例日程草稿，或手动增删日程；不会录音、调用模型、修改系统设置或连接 Hub。日程保存在 `test/.local/windows-preview/agenda.db`。同一命令末尾加 `Status` 或 `Stop` 可查看状态或关闭预览。

- Ubuntu 24.04 ARM64 ThinkSmart View，Python 3.12。
- PipeWire、`wpctl`、PortAudio、libopus、libspeexdsp，以及仓库中的唤醒词模型。
- 屏幕亮度和自动熄屏由系统镜像的屏幕管理程序执行，客户端写入 `/run/user/1000/thinksmart-screen.json`。
- WiFi 设置和屏幕键盘依赖设备展示屏外壳提供的 WebKit 消息通道。
- 摄像头采集需要 FFmpeg。内置传感器还需要系统层的 S5KC505A 驱动、CAMSS 和 libcamera 软件 ISP。

系统镜像的刷机与设备基础服务可参考 [thinksmart-ubuntu-image](https://github.com/justhalfbit/thinksmart-ubuntu-image)。

### 启动客户端

在已具备上述系统组件的 Ubuntu 设备上，按 `requirements.lock` 准备 Python 环境后运行

```sh
git clone https://github.com/lumia1998/xiaozhi-thinksmart-client.git
cd xiaozhi-thinksmart-client
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m thinksmart_client
```

生产服务模板位于 [deploy/thinksmart-client.service](deploy/thinksmart-client.service)，默认从 `/opt/thinksmart/client/current` 启动。部署时按实际发布目录准备 Python 环境、`models/` 和文件权限，再安装服务并重启 `thinksmart-client.service`。

## 连接 Hub

在设置页填写服务器地址，例如 `http://192.0.2.30:16688`。WebSocket 地址可自动推导，也可单独填写为 `ws://192.0.2.30:16689/xiaozhi/v1/`。设备配对令牌在 Hub 后台生成后填入客户端。

用户配置保存在 `~/.config/thinksmart-client/config.json`。主要字段包括 `hub_http`、`hub_ws`、`device_id`、`client_id`、`pairing_token`、`brightness`、`brightness_schedule` 和 `screen_off`。

Hub 的 `/kiosk/*` 接口当前执行本机来源检查。分开部署客户端与 Hub 时，语音 WebSocket 和带认证的视觉上传可独立工作，模型列表及服务器历史同步需要部署层提供受控的本机转发。客户端设置中的“检测连接”可用于排查接口可达性。

## 定时亮度

设置页分别保存“常规亮度”和“定时亮度”。定时亮度默认关闭，预设为每天 `00:00–06:00` 使用 `30%`。

```json
{
  "brightness": 80,
  "brightness_schedule": {
    "enabled": true,
    "start": "00:00",
    "end": "06:00",
    "level": 30
  }
}
```

这个示例在零点使用 30%，六点恢复 80%。关闭定时亮度后立即恢复常规亮度，重启后按设备本地时间重新计算。在时段内调整常规亮度，新的常规值于时段结束后应用。

睡眠时段单独控制主动播报，屏幕亮度由亮度设置控制。本地闹钟、倒计时和响铃提醒已从客户端移除。

## 多媒体与摄像头

客户端在握手中声明 `media` 能力，接收 Hub 下发的图片、视频和音频 HTTP(S) 直链。音视频由用户点击播放，播放期间暂停麦克风上传并保持亮屏，唤醒、新回复或结束操作会暂停媒体。实际可播放编码取决于设备浏览器。协议见 [Hub 多媒体文档](https://github.com/lumia1998/xiaozhi-hub-server/blob/main/hub-docs/media-output.md)。

`self.camera.take_photo` 按需拍摄并调用 Hub 的 `/mcp/vision/explain`，返回识别文字供模型回答。屏幕提示拍照与识别状态，打断、结束或断线会取消请求。

默认优先使用 `/opt/thinksmart-camera/bin/cam`，在 `/dev/shm` 临时目录中等待曝光稳定，采集结束后转为 JPEG 并清理文件。`camera_device` 可指定外接 V4L2 节点，通过 FFmpeg 采集。摄像头驱动、设备树、libcamera 和设备节点访问权限由系统层准备。

Hub 需配置 `selected_module.VLLM` 和对应 `VLLM` 提供商，视觉认证令牌通过 MCP 初始化获取。客户端向已配置的 Hub 地址上传图片。

## 本机接口

| 接口 | 用途 |
| --- | --- |
| `GET /events` | 状态、模型、对话、音量和蓝牙 SSE 事件 |
| `GET /api/history` | 获取客户端当前已载入的各模型聊天记录 |
| `POST /api/text` | 发送文字提问 |
| `POST /api/wake`、`/api/abort`、`/api/end` | 唤醒、打断、结束对话 |
| `POST /api/ptt/start`、`/api/ptt/stop` | 按住说话 |
| `POST /api/model` | 请求切换模型 |
| `POST /api/volume` | 设置设备音量 |
| `POST /api/brightness`、`/api/brightness-schedule` | 常规与定时亮度 |
| `POST /api/screen-off`、`/api/sleep` | 自动熄屏与睡眠时段 |
| `POST /api/media/playback` | 媒体播放状态和心跳 |
| `GET/POST /api/server`、`POST /api/server/check` | 服务器配置与连接检测 |
| `POST /api/history/clear`、`/api/bluetooth`、`/api/power-off` | 清理记录、蓝牙设置、关机 |

## 代码与测试

| 目录 | 内容 |
| --- | --- |
| `thinksmart_client/` | 设备状态机、音频、协议、MCP、摄像头和设置 |
| `web/v2/` | 当前屏幕界面及 Radix 色阶 |
| `models/kws-zh/` | 中文唤醒词模型 |
| `deploy/` | 客户端服务配置 |
| `test/` | Ubuntu 测试、依赖清单和测试配置 |
| `eval/` | 唤醒和延迟评测 |

测试步骤见 [test/README.md](test/README.md)。在 Ubuntu 上执行

```sh
python3 -m venv test/.venv
test/.venv/bin/python -m pip install -r test/requirements.txt
PYTHONDONTWRITEBYTECODE=1 test/.venv/bin/python -m pytest -c test/pytest.ini -q
```

测试产物使用 `test/.local/`、`test/.venv/` 和 `test/.pytest_cache/`。

## 来源与许可

[MIT License](LICENSE)。基于 [justhalfbit/xiaozhi-thinksmart-client](https://github.com/justhalfbit/xiaozhi-thinksmart-client) 的 ThinkSmart 客户端继续开发。

- WebRTC APM 封装来源于 py-xiaozhi，MIT，见 `thinksmart_client/webrtc_apm/LICENSE-py-xiaozhi`。
- 唤醒模型使用 sherpa-onnx 中文关键词识别模型，Apache-2.0。
- 厂商图标来自 LobeHub icons，MIT；Radix UI Colors 许可见 `web/v2/RADIX-LICENSE`。
- 仓库保留的历史铃声音频使用 Salamander Grand Piano V3 采样，Alexander Holm，CC BY 3.0，相关脚本位于 `tools/ringtone/`。

厂商名称及图标用于标识服务，相关商标归其所有者所有。

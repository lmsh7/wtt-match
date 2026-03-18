# wtt-match

从 WTT（世界乒乓球职业大联盟）YouTube 直播/回放视频中，通过 OCR 自动识别比赛对阵选手。

## 工作原理

1. 通过 yt-dlp 解析 YouTube 视频地址，获取远程流 URL
2. 通过 ffmpeg 按固定时间间隔抓取视频帧（直接从远程流 pipe 读取，无需下载视频）
3. 裁剪记分牌区域（ROI），经 CLAHE 增强 + 多种二值化处理
4. 使用 RapidOCR 识别文字，解析出选手姓名和比分
5. 输出结构化 JSON 结果

## 安装

### 前置依赖

```bash
brew install ffmpeg yt-dlp
```

### 安装项目

```bash
uv sync
```

## 使用

```bash
# 基本用法：每 10 分钟采样一帧
uv run wtt-match --url "https://www.youtube.com/watch?v=VIDEO_ID"

# 自定义采样间隔（秒）
uv run wtt-match --url "..." --interval 300

# 按选手名筛选结果（模糊匹配）
uv run wtt-match --url "..." --player "ZHANG"

# 保存调试帧图片
uv run wtt-match --url "..." --debug

# 指定输出目录和并行工作线程数
uv run wtt-match --url "..." --output results --workers 8

# 详细日志
uv run wtt-match --url "..." -v
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--url` | （必填） | YouTube 视频 URL |
| `--interval` | 600 | 帧采样间隔（秒） |
| `--player` | - | 按选手名筛选结果 |
| `--debug` | - | 保存预处理后的调试帧到 `output/debug/` |
| `--output` | `output` | 输出目录 |
| `--workers` | 4 | 并行线程数 |
| `-v` | - | 启用详细日志 |

### 输出示例

程序会在输出目录下生成 `matches.json`：

```json
{
  "video_url": "https://...",
  "total_frames": 42,
  "frames": [
    {
      "timestamp": 600.0,
      "time_fmt": "00:10:00",
      "player1": "WANG CHUQIN",
      "player2": "FAN ZHENDONG",
      "confidence": 0.87
    }
  ]
}
```

## 技术栈

- **yt-dlp** — YouTube 视频解析
- **ffmpeg** — 远程流帧抓取（pipe 模式，零磁盘 I/O）
- **OpenCV** — 图像预处理（CLAHE、二值化、锐化）
- **RapidOCR** — OCR 文字识别（ONNX Runtime 后端）
- **Python 3.11+** / **uv** — 包管理

# IndexTTS REST API

零样本声音克隆 + 语音合成 HTTP 服务。默认监听 **9880**，交互式文档：`/docs`（Swagger）、`/redoc`。

## 快速开始

```bash
# 仓库根目录
docker compose -f docker/docker-compose.yaml up -d

# 健康检查
curl http://127.0.0.1:9880/api/health
```

| 项 | 说明 |
|---|---|
| Base URL | `http://<host>:9880` |
| 默认模型 | IndexTTS-2.5（`--version 2.5`） |
| 音色目录 | `/app/prompts` → 宿主机 `docker/prompts` |
| 合成输出 | `/app/output` → 宿主机 `docker/output` |
| 音频外链 | 环境变量 `DOWNLOAD_URL`：把容器路径前缀 `/app/` 替换为该 URL |

`DOWNLOAD_URL` 示例：`http://192.168.3.21/`

| 容器内路径 | 对外 URL |
|---|---|
| `/app/prompts/alice.wav` | `http://192.168.3.21/prompts/alice.wav` |
| `/app/output/tts_xxx.wav` | `http://192.168.3.21/output/tts_xxx.wav` |

未配置 `DOWNLOAD_URL` 时回退为：

- 音色文件 → `/api/voices/{voice_id}/audio`
- 合成文件 → `/api/audio/{filename}`

---

## 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/clone` | 按 `audio_url` 下载参考音频并创建音色 |
| `GET` | `/api/voices` | 列出已保存音色 |
| `DELETE` | `/api/voices/{voice_id}` | 删除音色 |
| `GET` | `/api/voices/{voice_id}/audio` | 下载音色参考音频 |
| `POST` | `/api/tts` | 文本转语音 |
| `GET` | `/api/audio/{filename}` | 下载合成音频（本地回退） |

---

## GET `/api/health`

确认模型已加载、服务可用。

**响应** `200`

```json
{
  "status": "healthy",
  "model_version": "2.5",
  "model_loaded": true,
  "gpu_available": true,
  "vram_gb": 24.0,
  "half_precision": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 固定 `healthy` |
| `model_version` | string | `2` 或 `2.5` |
| `model_loaded` | bool | 模型是否已加载 |
| `gpu_available` | bool | 是否检测到 GPU |
| `vram_gb` | number \| null | 显存（GB） |
| `half_precision` | bool | 是否半精度 |

```bash
curl http://127.0.0.1:9880/api/health
```

---

## POST `/api/clone`

通过参考音频的 HTTP(S) URL 创建零样本音色。服务端自动下载（失败最多重试 **5** 次，HTTP Range **断点续传**），保存到 `prompts`，返回可访问的 `audio_url`。

**Content-Type:** `application/json`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `audio_url` | string | 是 | 参考音频地址（`http` / `https`），扩展名支持 `.wav` / `.mp3` / `.flac` / `.ogg` |
| `voice_id` | string | 否 | 音色名（仅字母数字、`_`、`-`）。省略则自动生成 |

```json
{
  "audio_url": "http://192.168.3.21/samples/alice.wav",
  "voice_id": "alice"
}
```

### 响应 `200`

```json
{
  "voice_id": "alice",
  "message": "Voice profile 'alice' saved (123456 bytes). Ready for TTS.",
  "audio_url": "http://192.168.3.21/prompts/alice.wav"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `voice_id` | string | 后续 TTS 使用的 ID |
| `message` | string | 提示信息 |
| `audio_url` | string | 本地保存后的访问地址（`DOWNLOAD_URL` 替换 `/app/`） |

### 错误

| HTTP | 说明 |
|------|------|
| `400` | `audio_url` 为空/非 http(s)，或 `voice_id` 非法 |
| `502` | 下载失败（已重试 5 次） |

```bash
curl -X POST http://127.0.0.1:9880/api/clone \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "http://192.168.3.21/samples/alice.wav",
    "voice_id": "alice"
  }'
```

---

## GET `/api/voices`

列出全部已保存音色。

**响应** `200`

```json
{
  "voices": [
    {
      "voice_id": "alice",
      "filename": "alice.wav",
      "size_bytes": 123456
    }
  ],
  "count": 1
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `voices` | array | 音色列表 |
| `voices[].voice_id` | string | 音色 ID |
| `voices[].filename` | string | 文件名 |
| `voices[].size_bytes` | int | 文件大小 |
| `count` | int | 数量 |

```bash
curl http://127.0.0.1:9880/api/voices
```

---

## DELETE `/api/voices/{voice_id}`

按 ID 删除音色。

**响应** `200`

```json
{
  "message": "Voice profile 'alice' deleted"
}
```

### 错误

| HTTP | 说明 |
|------|------|
| `404` | 音色不存在 |

```bash
curl -X DELETE http://127.0.0.1:9880/api/voices/alice
```

---

## GET `/api/voices/{voice_id}/audio`

下载该音色的参考音频。

### 错误

| HTTP | 说明 |
|------|------|
| `404` | 音色不存在 |

```bash
curl -OJ http://127.0.0.1:9880/api/voices/alice/audio
```

---

## POST `/api/tts`

使用已克隆音色合成语音。

**Content-Type:** `application/json`

### 请求体

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `voice_id` | string | 是 | — | `/api/clone` 返回的 ID，或 `prompts` 中已有文件名（无扩展名） |
| `text` | string | 是 | — | 待合成文本（不能为空） |
| `lang` | string | 否 | `ZH` | v2.5：`ZH` / `EN` / `JA` / `AR` / `ES`；v2：`ZH` / `EN` |
| `speed` | number | 否 | `1.0` | 语速（`duration_factor`）：`<1` 更快，`>1` 更慢 |
| `do_sample` | bool | 否 | `true` | 是否采样解码 |
| `top_p` | number | 否 | `0.8` | nucleus sampling |
| `top_k` | int | 否 | `30` | top-k；`≤0` 表示禁用 |
| `temperature` | number | 否 | `0.8` | 采样温度 |
| `length_penalty` | number | 否 | `0.0` | 长度惩罚 |
| `num_beams` | int | 否 | `3` | beam search 数量 |
| `repetition_penalty` | number | 否 | `10.0` | 重复惩罚 |
| `max_mel_tokens` | int | 否 | `1500` | 最大 mel token 数 |
| `max_text_tokens_per_segment` | int | 否 | `120` | 每段最大文本 token 数 |

### 响应 `200`

```json
{
  "audio_url": "http://192.168.3.21/output/tts_1710000000_ab12cd.wav",
  "duration_seconds": 3.42
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `audio_url` | string | 可访问下载地址（`DOWNLOAD_URL` 外链，或回退 `/api/audio/{filename}`） |
| `duration_seconds` | number \| null | 推理耗时（秒），**不是**音频时长 |

### 错误

| HTTP | 说明 |
|------|------|
| `400` | 文本为空，或 `lang` 不支持 |
| `404` | `voice_id` 不存在 |
| `500` | 推理失败或输出文件未生成 |

```bash
curl -X POST http://127.0.0.1:9880/api/tts \
  -H "Content-Type: application/json" \
  -d '{
    "voice_id": "alice",
    "text": "你好，欢迎使用 IndexTTS。",
    "lang": "ZH",
    "speed": 1.0
  }'
```

英文示例：

```bash
curl -X POST http://127.0.0.1:9880/api/tts \
  -H "Content-Type: application/json" \
  -d '{
    "voice_id": "alice",
    "text": "Hello, welcome to IndexTTS.",
    "lang": "EN"
  }'
```

---

## GET `/api/audio/{filename}`

按文件名下载 `output` 目录中的合成 WAV。未配置 `DOWNLOAD_URL` 时，TTS 的 `audio_url` 会指向此接口。

### 错误

| HTTP | 说明 |
|------|------|
| `404` | 文件不存在 |

```bash
curl -OJ http://127.0.0.1:9880/api/audio/tts_1710000000_ab12cd.wav
```

---

## 典型调用流程

```text
1. POST /api/clone   → voice_id + audio_url（音色已落盘）
2. GET  /api/voices  → 确认列表（可选）
3. POST /api/tts     → audio_url（合成音频）
4. GET  audio_url    → 下载 / 播放
```

Python 示例：

```python
import requests

BASE = "http://127.0.0.1:9880"

# 1. 克隆音色（服务端按 URL 下载）
r = requests.post(
    f"{BASE}/api/clone",
    json={
        "audio_url": "http://192.168.3.21/samples/alice.wav",
        "voice_id": "alice",
    },
)
r.raise_for_status()
data = r.json()
voice_id = data["voice_id"]
print("clone:", data["audio_url"])

# 2. 合成
r = requests.post(
    f"{BASE}/api/tts",
    json={
        "voice_id": voice_id,
        "text": "今天天气不错。",
        "lang": "ZH",
        "speed": 1.0,
    },
)
r.raise_for_status()
print("tts:", r.json()["audio_url"])
print("elapsed:", r.json()["duration_seconds"], "s")
```

---

## 说明

- 推理串行执行（进程内锁），并发请求会排队。
- CORS 已放开（`*`），可直接从浏览器调用。
- 在线调试：`http://<host>:9880/docs`。
- 使用 `DOWNLOAD_URL` 时，需用 Nginx 等把 `/prompts/`、`/output/` 分别映射到宿主机的 `prompts`、`output` 目录。

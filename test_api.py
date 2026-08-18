#!/usr/bin/env python3
"""
IndexTTS API 端到端测试脚本
============================
测试流程：
  1. 检查 API 健康状态（等待模型加载完成）
  2. 列出已有声音
  3. 通过 API 克隆声音（上传参考音频）
  4. 用克隆的声音生成语音
  5. 下载生成的音频文件

用法：
  python test_api.py [--host localhost] [--port 9880]
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

def parse_args():
    parser = argparse.ArgumentParser(description="IndexTTS API end-to-end test")
    parser.add_argument("--host", default="localhost", help="API host")
    parser.add_argument("--port", type=int, default=9880, help="API port")
    parser.add_argument("--ref-audio", default=None, help="Reference audio file to clone")
    parser.add_argument("--text", default="你好，这是一个声音克隆和语音合成的测试。", help="Text to synthesize")
    parser.add_argument("--lang", default="ZH", help="Language: ZH, EN, JA, AR, ES")
    parser.add_argument("--voice-id", default=None, help="Use existing voice_id instead of uploading")
    parser.add_argument("--output", default="test_output.wav", help="Output audio filename")
    return parser.parse_args()

BASE_URL = ""

def api_get(path):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def api_post_json(path, data):
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode())

def api_post_file(path, file_path, voice_id=None):
    """Upload a file using multipart/form-data (manual boundary construction)."""
    url = f"{BASE_URL}{path}"
    boundary = "----TestBoundary" + str(int(time.time()))
    
    with open(file_path, "rb") as f:
        file_data = f.read()
    
    filename = os.path.basename(file_path)
    
    body = b""
    # voice_id field (optional)
    if voice_id:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="voice_id"\r\n\r\n'.encode()
        body += f"{voice_id}\r\n".encode()
    
    # file field
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    body += b"Content-Type: audio/wav\r\n\r\n"
    body += file_data
    body += f"\r\n--{boundary}--\r\n".encode()
    
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())

def download_file(url, output_path):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    with open(output_path, "wb") as f:
        f.write(data)
    return len(data)

def wait_for_api(max_wait=3600, interval=10):
    """Poll /api/health until the model is loaded."""
    print(f"Waiting for API at {BASE_URL}/api/health ...")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            result = api_get("/api/health")
            if result.get("model_loaded"):
                print(f"  API is ready! Model: v{result.get('model_version')}, "
                      f"GPU: {result.get('vram_gb')}GB, FP16: {result.get('half_precision')}")
                return True
            else:
                elapsed = int(time.time() - start)
                print(f"  Model still loading... ({elapsed}s elapsed)")
        except urllib.error.URLError:
            elapsed = int(time.time() - start)
            print(f"  API not reachable yet... ({elapsed}s elapsed)")
        except Exception as e:
            elapsed = int(time.time() - start)
            print(f"  Waiting... ({elapsed}s elapsed) {e}")
        time.sleep(interval)
    
    print("ERROR: Timed out waiting for API.")
    return False

def main():
    global BASE_URL
    args = parse_args()
    BASE_URL = f"http://{args.host}:{args.port}"
    
    print("=" * 60)
    print("IndexTTS API End-to-End Test")
    print("=" * 60)
    
    # Step 1: Wait for API to be ready
    print("\n[1/5] Checking API health...")
    if not wait_for_api():
        sys.exit(1)
    
    # Step 2: List existing voices
    print("\n[2/5] Listing existing voice profiles...")
    voices = api_get("/api/voices")
    print(f"  Found {voices['count']} voice profile(s):")
    for v in voices["voices"]:
        print(f"    - {v['voice_id']} ({v['filename']}, {v['size_bytes']} bytes)")
    
    # Step 3: Clone a voice (upload) or use an existing one
    voice_id = args.voice_id
    ref_audio = args.ref_audio

    if ref_audio:
        # Upload reference audio to clone a voice
        if not os.path.exists(ref_audio):
            print(f"  ERROR: Reference audio not found: {ref_audio}")
            sys.exit(1)

        clone_name = voice_id or f"test_clone_{int(time.time())}"
        print(f"\n[3/5] Cloning voice from: {ref_audio}")
        result = api_post_file("/api/clone", ref_audio, voice_id=clone_name)
        voice_id = result["voice_id"]
        print(f"  Cloned successfully! voice_id='{voice_id}'")
        print(f"  Message: {result['message']}")
    elif voice_id:
        print(f"\n[3/5] Using specified voice_id: '{voice_id}'")
    elif voices["voices"]:
        voice_id = voices["voices"][0]["voice_id"]
        print(f"\n[3/5] Using existing voice profile: '{voice_id}'")
    else:
        print("\n[3/5] No voice profiles found and no --ref-audio provided.")
        print("  Usage: python test_api.py --ref-audio path/to/audio.wav")
        sys.exit(1)
    
    # Step 4: Generate speech
    print(f"\n[4/5] Generating speech...")
    print(f"  Voice: {voice_id}")
    print(f"  Text:  {args.text}")
    print(f"  Lang:  {args.lang}")
    
    tts_request = {
        "voice_id": voice_id,
        "text": args.text,
        "lang": args.lang,
        "speed": 1.0,
    }
    
    result = api_post_json("/api/tts", tts_request)
    audio_url = result["audio_url"]
    duration = result.get("duration_seconds")
    
    print(f"  Generated in {duration}s")
    print(f"  Audio URL: {BASE_URL}{audio_url}")
    
    # Step 5: Download the audio
    print(f"\n[5/5] Downloading audio...")
    output_path = args.output
    file_size = download_file(f"{BASE_URL}{audio_url}", output_path)
    print(f"  Saved to: {output_path} ({file_size} bytes)")
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)
    print(f"\nVoice cloned:  {voice_id}")
    print(f"Speech file:   {output_path}")
    print(f"API base URL:  {BASE_URL}")
    print(f"API docs:      {BASE_URL}/docs")

if __name__ == "__main__":
    main()

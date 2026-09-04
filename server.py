# -*- coding: utf-8 -*-
"""WebPlayer 本地服务器: 静态文件 + Range 支持 (http://localhost:8790)"""
import http.server, socketserver, os, re, sys, json, subprocess, threading, base64, hashlib
import wave, io, asyncio, time

PORT = 8790
WS_PORT = 8791
ROOT = os.path.dirname(os.path.abspath(__file__))
VOSK_MODEL = r"C:\Users\Administrator\.qclaw\workspace\vosk-model"

import websockets
import vosk
_MODEL = None
_MODEL_LOCK = threading.Lock()
def get_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            _MODEL = vosk.Model(VOSK_MODEL)
        return _MODEL

# 与网页端 execVoice 命令表对齐的语法约束词表
GRAMMAR = ["start","pause","pad","pluck","bass","bell","drum","man","no","unknown",
           "[unk]"]

async def ws_handler(ws):
    """每客户端独立流式识别器: 浏览器推 16k mono s16 PCM 二进制帧
    vosk 0.3.45: AcceptWaveform 返回 int (0=partial, 1=final);
    流式过程中 final 很少触发 → 用 partial 停止更新 0.8s 后 Flush"""
    rec = vosk.KaldiRecognizer(get_model(), 16000, json.dumps(GRAMMAR))
    rec.SetWords(True)
    last_partial = ""
    last_change = time.time()
    try:
        async for pcm in ws:
            if not isinstance(pcm, bytes):
                continue
            code = rec.AcceptWaveform(pcm)
            fin = (code == 1) or (isinstance(code, tuple) and code[0])
            now = time.time()
            if fin:
                text = json.loads(rec.FinalResult()).get("text", "").strip()
                last_partial = ""; last_change = now
                if text and text not in ("unknown", "[unk]"):
                    await ws.send(json.dumps({"t": "cmd", "text": text}))
                continue
            partial = json.loads(rec.PartialResult()).get("partial", "").strip()
            if partial and partial not in ("unknown", "[unk]"):
                if partial != last_partial:
                    # partial 在变 → 还在说话
                    last_partial = partial
                    last_change = now
                    await ws.send(json.dumps({"t": "partial", "text": partial}))
                elif now - last_change > 0.8:
                    # partial 停止更新 0.8s → 语音段结束, flush 成 final
                    text = json.loads(rec.FinalResult()).get("text", "").strip()
                    last_partial = ""; last_change = now
                    if text and text not in ("unknown", "[unk]"):
                        await ws.send(json.dumps({"t": "cmd", "text": text}))
            else:
                # 无 partial: 若之前有 → 同样在 0.8s 静音后 flush
                if last_partial and now - last_change > 0.8:
                    text = json.loads(rec.FinalResult()).get("text", "").strip()
                    last_partial = ""; last_change = now
                    if text and text not in ("unknown", "[unk]"):
                        await ws.send(json.dumps({"t": "cmd", "text": text}))
    except websockets.ConnectionClosed:
        pass

def run_ws():
    async def main():
        get_model()
        async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT, max_size=2**20):
            print(f"WS STT on ws://localhost:{WS_PORT}")
            await asyncio.Future()   # run forever
    asyncio.run(main())


# ---------- /stt-ws 同端口 WebSocket (手写帧协议) ----------
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def _ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()

def _ws_read_frame(rfile):
    b = rfile.read(2)
    if not b or len(b) < 2:
        return None, None
    opcode = b[0] & 0x0F
    masked = bool(b[1] & 0x80)
    ln = b[1] & 0x7F
    if ln == 126:
        ln = int.from_bytes(rfile.read(2), "big")
    elif ln == 127:
        ln = int.from_bytes(rfile.read(8), "big")
    mk = rfile.read(4) if masked else None
    data = rfile.read(ln) if ln else b""
    if masked and data:
        data = bytes(c ^ mk[i % 4] for i, c in enumerate(data))
    return opcode, data

def _ws_send(conn, opcode, payload):
    n = len(payload)
    h = bytes([0x80 | opcode])
    if n < 126:
        h += bytes([n])
    elif n < 65536:
        h += bytes([126]) + n.to_bytes(2, "big")
    else:
        h += bytes([127]) + n.to_bytes(8, "big")
    conn.sendall(h + payload)

def handle_stt_ws(handler):
    """在 http.server 连接上完成 WS 升级并跑语音识别会话(逻辑与 8791 版一致)"""
    key = handler.headers.get("Sec-WebSocket-Key", "")
    if not key:
        handler.send_error(400, "missing websocket key")
        return
    resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Accept: " + _ws_accept(key) + "\r\n\r\n")
    handler.connection.sendall(resp.encode("latin-1"))
    handler.close_connection = True

    rec = vosk.KaldiRecognizer(get_model(), 16000, json.dumps(GRAMMAR))
    rec.SetWords(True)
    last_partial = ""
    last_change = time.time()
    rfile, conn = handler.rfile, handler.connection
    try:
        while True:
            opcode, data = _ws_read_frame(rfile)
            if opcode is None:
                break
            if opcode == 8:  # close
                try: _ws_send(conn, 8, data[:125])
                except Exception: pass
                break
            if opcode == 9:  # ping -> pong
                _ws_send(conn, 10, data)
                continue
            if opcode != 2 or not data:  # 只处理二进制音频帧
                continue
            code = rec.AcceptWaveform(data)
            now = time.time()
            if code == 1:
                text = json.loads(rec.FinalResult()).get("text", "").strip()
                last_partial = ""; last_change = now
                if text and text not in ("unknown", "[unk]"):
                    _ws_send(conn, 1, json.dumps({"t": "cmd", "text": text}).encode())
                continue
            partial = json.loads(rec.PartialResult()).get("partial", "").strip()
            if partial and partial not in ("unknown", "[unk]"):
                if partial != last_partial:
                    last_partial = partial; last_change = now
                    _ws_send(conn, 1, json.dumps({"t": "partial", "text": partial}).encode())
                elif now - last_change > 0.8:
                    text = json.loads(rec.FinalResult()).get("text", "").strip()
                    last_partial = ""; last_change = now
                    if text and text not in ("unknown", "[unk]"):
                        _ws_send(conn, 1, json.dumps({"t": "cmd", "text": text}).encode())
            else:
                if last_partial and now - last_change > 0.8:
                    text = json.loads(rec.FinalResult()).get("text", "").strip()
                    last_partial = ""; last_change = now
                    if text and text not in ("unknown", "[unk]"):
                        _ws_send(conn, 1, json.dumps({"t": "cmd", "text": text}).encode())
    except Exception as e:
        print("[stt-ws] session end:", type(e).__name__, str(e)[:120])

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path in ("/stt-ws", "/stt-ws/"):
            if self.headers.get("Upgrade", "").lower() == "websocket":
                handle_stt_ws(self)
            else:
                self.send_error(400, "expected websocket upgrade")
            return
        f = self.send_head()
        if f:
            try:
                self.copyfile(f, self.wfile)
            finally:
                f.close()

    def do_HEAD(self):
        if self.path in ("/stt-ws", "/stt-ws/"):
            self.send_error(400)
            return
        f = self.send_head()
        if f:
            f.close()

    # ---------- 语音识别 POST /stt ----------
    def do_POST(self):
        if self.path != "/stt":
            self.send_error(404, "no such api")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            webm = self.rfile.read(length)
            if not webm:
                self._json({"text": "", "err": "empty"})
                return
            # webm/opus → 16k mono wav (ffmpeg)
            p = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                input=webm, capture_output=True)
            wav = p.stdout
            if not wav or p.returncode != 0:
                self._json({"text": "", "err": "ffmpeg: " + p.stderr.decode("utf-8", "ignore")[:200]})
                return
            wf = wave.open(io.BytesIO(wav), "rb")
            rec = vosk.KaldiRecognizer(get_model(), wf.getframerate())
            rec.SetWords(True)
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                rec.AcceptWaveform(data)
            res = json.loads(rec.FinalResult())
            self._json({"text": res.get("text", "").strip()})
        except Exception as e:
            self._json({"text": "", "err": str(e)[:200]})

    def _json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
            if not os.path.exists(path):
                self.send_error(404, "no index")
                return None
        ctype = self.guess_type(path)
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "not found")
            return None
        size = os.fstat(f.fileno()).st_size
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m and (m.group(1) or m.group(2)):
                start = int(m.group(1)) if m.group(1) else 0
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                f.seek(start)
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self._range = (f, start, end)
                return RangeWrapper(f, start, end)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return f

class RangeWrapper:
    def __init__(self, f, start, end):
        self.f = f; self.left = end - start + 1
    def read(self, n=65536):
        if self.left <= 0: return b""
        chunk = self.f.read(min(n, self.left))
        self.left -= len(chunk)
        return chunk
    def close(self): self.f.close()
    def __iter__(self):
        while True:
            c = self.read()
            if not c: break
            yield c

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    # 预热 Vosk 模型 + 启动 WS 流式识别线程
    threading.Thread(target=lambda: get_model(), daemon=True).start()
    threading.Thread(target=run_ws, daemon=True).start()
    print(f"WebPlayer serving http://localhost:{PORT}  root={ROOT}")
    with Server(("", PORT), H) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass

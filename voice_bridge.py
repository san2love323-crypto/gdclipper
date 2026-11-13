"""
아키텍트 음성 브릿지 — Simple Voice Chat 모드(architect-voice.jar) ↔ 아키텍트
  플레이어 목소리 → Groq Whisper(무료) → 글자 → 아키텍트(채팅과 똑같이 처리: 명령/게임/대화)
  아키텍트 말 → TTS → 게임 안 공간음 (각 플레이어 머리 위 3블록에서 들림)

재밌는 것들 (config voice 에서 on/off):
  - 호출: "아키텍트, 밤으로 바꿔" 말로 하면 됨 (주인은 명령, 남은 대화)
  - 소리 지르면 벌: 크게 1초 이상 소리치면 머리 위 번개 (shout_event)
  - 음성 비밀번호: "!암호문 설정 열려라참깨 x y z" 해두면 그 근처에서 말하면 문(블록) 사라짐
  - 음성 게임: 퀴즈/끝말잇기/숫자맞추기 전부 말로 참여 가능 (채팅 게임이 그대로 음성 게임)
  - 속삭임: 작게 말하면 아키텍트가 그 사람에게만 작게 대답
  - 따라하기: "!따라해" 켜면 아키텍트가 네 말을 로봇 목소리로 따라함 (장난용)
"""
import asyncio
import io
import json
import re
import socket
import struct
import threading
import time
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

HERE = Path(__file__).resolve().parent
SR = 48000


def log(m): print(f"{time.strftime('%H:%M:%S')} 🎙 {m}", flush=True)


class VoiceBridge:
    def __init__(self, admin):
        self.a = admin; self.cfg = admin.cfg
        v = self.cfg.setdefault("voice", {})
        for k, val in {"enabled": True, "port": 8792, "tts": "edge", "edge_voice": "ko-KR-InJoonNeural", "rate": "+10%",
                       "speak_announcements": True, "shout_event": True, "shout_rms": 9000, "shout_sec": 1.0,
                       "silence_sec": 0.9, "min_utter_sec": 0.6, "wake_required": True, "whisper_rms": 1200, "parrot": []}.items():
            v.setdefault(k, val)
        self.v = v
        self.conn = None; self.lock = threading.Lock()
        self.buf = defaultdict(list); self.last_pkt = {}; self.loud_since = {}; self.rms_hist = defaultdict(list)
        self.doors = self.cfg.setdefault("voice_doors", [])   # [{"phrase":"열려라참깨","x":..,"y":..,"z":..,"radius":6}]
        self.key = self.cfg["groq"]["api_key"]
        self._tts_cache = {}
        # 아키텍트 말을 음성으로도
        orig_say, orig_whisper = admin.say, admin.whisper
        def say(text):
            orig_say(text)
            if self.v["speak_announcements"] and self.conn: threading.Thread(target=self.speak_all, args=(text,), daemon=True).start()
        def whisper(player, text):
            orig_whisper(player, text)
            if self.conn: threading.Thread(target=self.speak_to, args=(player, text, 2.2), daemon=True).start()
        admin.say = say; admin.whisper = whisper

    # ================= 소켓 서버 =================
    def start(self):
        threading.Thread(target=self._server, daemon=True).start()
        threading.Thread(target=self._flusher, daemon=True).start()
        log(f"브릿지 대기 중: 포트 {self.v['port']} (모드가 접속하면 연결됨)")

    def _server(self):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", self.v["port"])); s.listen(1)
        while True:
            c, addr = s.accept(); c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log(f"모드 연결됨 {addr}"); self.conn = c
            try: self._reader(c)
            except Exception as e: log(f"연결 끊김: {e}")
            finally: self.conn = None

    def _recvn(self, c, n):
        b = b""
        while len(b) < n:
            ch = c.recv(n - len(b))
            if not ch: raise ConnectionError("closed")
            b += ch
        return b

    def _reader(self, c):
        while True:
            (hl,) = struct.unpack(">i", self._recvn(c, 4))
            h = json.loads(self._recvn(c, hl).decode("utf-8"))
            payload = self._recvn(c, h.get("len", 0)) if h.get("len") else b""
            if h["t"] == "audio": self._on_audio(h["player"], payload)
            elif h["t"] == "hello": log("모드 인사 받음"); self.a.say("음성 회선 연결. 이제 말로 해도 듣는다.")

    def send(self, header: dict, payload: bytes = b""):
        c = self.conn
        if not c: return
        header["len"] = len(payload); hb = json.dumps(header).encode("utf-8")
        with self.lock:
            try: c.sendall(struct.pack(">i", len(hb)) + hb + payload)
            except Exception as e: log(f"전송 실패: {e}")

    # ================= 수신: 말 끝 감지 =================
    def _on_audio(self, player, pcm_bytes):
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        self.buf[player].append(pcm); self.last_pkt[player] = time.time()
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if len(pcm) else 0
        self.rms_hist[player].append(rms); self.rms_hist[player] = self.rms_hist[player][-50:]
        # 소리 지르기 감지
        if self.v["shout_event"]:
            if rms > self.v["shout_rms"]:
                self.loud_since.setdefault(player, time.time())
                if time.time() - self.loud_since[player] > self.v["shout_sec"]:
                    self.loud_since[player] = time.time() + 10   # 10초 쿨다운
                    self.a.rcon.cmd(f"execute at {player} run summon minecraft:lightning_bolt ~2 ~ ~2")
                    self.a.whisper(player, "⚡ 시끄럽다.")
            else: self.loud_since.pop(player, None)

    def _flusher(self):
        while True:
            time.sleep(0.2); now = time.time()
            for p in list(self.last_pkt):
                if now - self.last_pkt[p] >= self.v["silence_sec"] and self.buf[p]:
                    pcm = np.concatenate(self.buf[p]); self.buf[p] = []; del self.last_pkt[p]
                    if len(pcm) / SR >= self.v["min_utter_sec"]:
                        threading.Thread(target=self._utterance, args=(p, pcm), daemon=True).start()

    # ================= 발화 처리 =================
    def _utterance(self, player, pcm48):
        rms = float(np.sqrt(np.mean(pcm48.astype(np.float32) ** 2)))
        text = self.stt(pcm48)
        if not text: return
        log(f"<{player}> 🎤 {text}  (rms {int(rms)})")
        is_owner = player in self.cfg["owner_names"]
        # 음성 비밀번호 문
        for d in self.doors:
            if d["phrase"].replace(" ", "") in text.replace(" ", ""):
                r = self.a.rcon.cmd(f"execute as {player} at @s if entity @s[x={d['x']},y={d['y']},z={d['z']},distance=..{d.get('radius', 6)}]")
                if "passed" in r.lower():
                    self.a.rcon.cmd(f"setblock {d['x']} {d['y']} {d['z']} minecraft:air"); self.a.rcon.cmd(f"setblock {d['x']} {int(d['y'])+1} {d['z']} minecraft:air")
                    self.a.rcon.cmd(f"execute positioned {d['x']} {d['y']} {d['z']} run playsound minecraft:block.iron_door.open master @a ~ ~ ~ 1 0.6")
                    self.speak_to(player, "열렸다.", 2.2); return
        # 따라하기
        if player in self.v["parrot"]:
            self.speak_to(player, text, 2.2); return
        # 속삭임: 작게 말하면 아키텍트도 그 사람에게만
        whisper_mode = rms < self.v["whisper_rms"]
        aliases = self.cfg["aliases"]
        called = any(text.replace(" ", "").startswith(a) for a in aliases)
        if self.v["wake_required"] and not called and not text.startswith("!"):
            # 호출 안 했으면 진행 중 게임 입력으로만 사용 (퀴즈 정답 등)
            self.a.games.on_chat(player, text); return
        q = re.sub(r"^(" + "|".join(map(re.escape, aliases)) + r")\s*[,아야]?\s*", "", text.strip())
        if text.startswith("!") or (is_owner and q.startswith("!")):
            self.a.on_chat(player, q if q.startswith("!") else "!" + q); return
        if whisper_mode:
            reply = self.a.brain.ask(player, q + " (속삭이듯 작게 말함)", is_owner)
            self.a.whisper(player, reply)
        else:
            reply = self.a.brain.ask(player, q, is_owner)
            self.a.say(reply)

    # ================= STT (Groq Whisper) =================
    def stt(self, pcm48: np.ndarray) -> str:
        if not self.key: return ""
        pcm16 = pcm48.reshape(-1, 3).mean(axis=1).astype(np.int16) if len(pcm48) % 3 == 0 else pcm48[::3]
        bio = io.BytesIO()
        with wave.open(bio, "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm16.tobytes())
        try:
            r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {self.key}"},
                              files={"file": ("a.wav", bio.getvalue(), "audio/wav")},
                              data={"model": "whisper-large-v3-turbo", "language": "ko", "temperature": "0", "prompt": "아키텍트, 공, 마인크래프트 서버 명령"}, timeout=30)
            if r.status_code == 404:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {self.key}"},
                                  files={"file": ("a.wav", bio.getvalue(), "audio/wav")}, data={"model": "whisper-large-v3", "language": "ko"}, timeout=30)
            t = r.json().get("text", "").strip()
            if re.fullmatch(r"(시청해주셔서 감사합니다|감사합니다|자막.*|\.+)", t): return ""
            return t
        except Exception as e:
            log(f"STT 오류: {e}"); return ""

    # ================= TTS =================
    def tts_pcm48(self, text: str) -> np.ndarray | None:
        text = re.sub(r"[*_`#>|~§]|\[.*?\]", "", text).strip()[:300]
        if not text: return None
        if text in self._tts_cache: return self._tts_cache[text]
        pcm = None
        if self.v["tts"] == "edge":
            pcm = self._edge(text)
        if pcm is None:
            pcm = self._sapi(text)
        if pcm is not None and len(self._tts_cache) < 200: self._tts_cache[text] = pcm
        return pcm

    def _edge(self, text):
        try:
            import edge_tts, miniaudio
            async def gen():
                c = edge_tts.Communicate(text, self.v["edge_voice"], rate=self.v["rate"]); out = b""
                async for ch in c.stream():
                    if ch["type"] == "audio": out += ch["data"]
                return out
            mp3 = asyncio.run(gen())
            dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=SR)
            return np.frombuffer(dec.samples, dtype=np.int16)
        except Exception as e:
            log(f"edge-tts 실패({e}) → 윈도우 음성"); return None

    def _sapi(self, text):
        try:
            import pyttsx3, tempfile, os
            eng = pyttsx3.init()
            for vv in eng.getProperty("voices"):
                if "ko" in vv.id.lower() or "Heami" in vv.name: eng.setProperty("voice", vv.id); break
            fd, path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            eng.save_to_file(text, path); eng.runAndWait()
            with wave.open(path, "rb") as w:
                sr, ch, raw = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
            os.unlink(path)
            pcm = np.frombuffer(raw, dtype=np.int16)
            if ch == 2: pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
            if sr != SR:
                idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * SR / sr)); pcm = np.interp(idx, np.arange(len(pcm)), pcm).astype(np.int16)
            return pcm
        except Exception as e:
            log(f"TTS 실패: {e}"); return None

    def speak_all(self, text, y=3.0):
        pcm = self.tts_pcm48(text)
        if pcm is not None: self.send({"t": "speak_all", "y_offset": y}, pcm.tobytes())

    def speak_to(self, player, text, y=3.0):
        pcm = self.tts_pcm48(text)
        if pcm is not None: self.send({"t": "speak", "player": player, "y_offset": y}, pcm.tobytes())

    # ================= 채팅 명령 (features 에서 호출) =================
    def on_chat(self, p, c, arg):
        if c == "암호문":
            parts = arg.split()
            if len(parts) >= 4 and parts[0] == "설정":
                phrase, x, y, z = parts[1], parts[2], parts[3], parts[4] if len(parts) > 4 else None
                if z is None: return "사용법: !암호문 설정 <문구> x y z"
                self.doors.append({"phrase": phrase, "x": x, "y": y, "z": z, "radius": 6}); self._save(); return f"🔐 ({x},{y},{z}) 블록은 '{phrase}' 라고 말하면 열린다"
            if parts and parts[0] == "삭제": self.doors.clear(); self._save(); return "암호문 전부 삭제"
            return "현재 암호문: " + (", ".join(f"'{d['phrase']}'@({d['x']},{d['y']},{d['z']})" for d in self.doors) or "없음") + "  | !암호문 설정 <문구> x y z"
        if c == "따라해":
            if p in self.v["parrot"]: self.v["parrot"].remove(p); self._save(); return "따라하기 끔"
            self.v["parrot"].append(p); self._save(); return "따라하기 켬 (다시 치면 끔)"
        if c == "음성":
            if arg in ("꺼", "off"): self.v["speak_announcements"] = False
            elif arg in ("켜", "on"): self.v["speak_announcements"] = True
            self._save(); return f"아키텍트 음성 {'켜짐' if self.v['speak_announcements'] else '꺼짐'} | 연결: {'O' if self.conn else 'X'}"
        return None

    def _save(self):
        from mc_admin import save_cfg; save_cfg(self.cfg)


def start(admin):
    vb = VoiceBridge(admin)
    if not vb.v["enabled"]: return None
    vb.start(); return vb

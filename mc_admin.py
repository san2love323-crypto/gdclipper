#!/usr/bin/env python3
"""
아키텍트 — 마인크래프트 서버를 지배하는 AI 관리자 (공의 마크 분신)

하는 일
  👁  latest.log 실시간 감시: 채팅 / 접속 / 퇴장 / 사망 / 도전과제
  🧠  Groq 뇌: 플레이어가 "아키텍트" 또는 "공" 부르면 대답, 규칙 위반 판단, 주인 명령 해석
  ⚡  RCON으로 명령 실행 (time, weather, give, tp, kick, summon, tellraw ...)
  📜  규칙 엔진: 욕설/광고/스팸 → 경고 → 3회 킥 (경고 횟수 기억)
  ⏰  트리거: "밤 되면 좀비 두 배", "5명 넘으면 환영 불꽃", 시간/인원/이벤트 조건
  💬  디스코드 브릿지(선택): 마크 채팅 ↔ 디스코드, 디스코드에서 관리 명령
  📱  HTTP 입구: 폰 공이 "아키텍트한테 ~ 시켜" 하면 여기로 옴

설정: config.json (처음 실행하면 생성됨). 서버 server.properties 에 아래 3줄 필요:
  enable-rcon=true
  rcon.port=25575
  rcon.password=원하는비번
"""
import asyncio
import json
import os
import re
import socket
import struct
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from games import GameManager
from features import Features

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"

DEFAULT_CFG = {
    "name": "아키텍트",
    "aliases": ["아키텍트", "공", "관리자", "architect"],
    "owner_names": ["유준"],                      # 마크 닉네임. 이 사람 말은 전부 '명령'으로 처리
    "rcon": {"host": "127.0.0.1", "port": 25575, "password": "바꿔주세요"},
    "log_path": "C:/Users/이유준/curseforge/minecraft/Instances/Cobbleverse/logs/latest.log",
    "groq": {"api_key": "", "model": "auto"},
    "persona": "너는 '아키텍트'. 이 마인크래프트 서버를 관장하는 AI 관리자. 매트릭스의 아키텍트처럼 침착하고 전지적이지만, 플레이어들에겐 은근히 다정하다. 한국어, 1~2문장, 가끔 서버 세계관 드립.",
    "rules": [
        "욕설, 비하, 차별 발언 금지",
        "광고, 다른 서버 홍보 금지",
        "같은 말 도배(스팸) 금지",
        "그리핑(남의 건축물 파괴) 금지",
        "핵/치트 클라이언트 금지"
    ],
    "warn_limit": 3,                               # 경고 3회 → 킥
    "auto_moderation": True,                       # False면 경고만 알리고 자동 킥 안 함
    "triggers": [
        {"name": "밤 좀비 두배", "when": "night", "enabled": False,
         "commands": ["execute as @a at @s run summon minecraft:zombie ~3 ~ ~3", "execute as @a at @s run summon minecraft:zombie ~-3 ~ ~-3"],
         "say": "밤이 왔다. 좀비가 두 배로 몰려온다.", "cooldown_sec": 600},
        {"name": "환영 인사", "when": "join", "enabled": True, "commands": [], "say": "{player}, 아키텍트가 너를 지켜보고 있다. 환영한다."},
        {"name": "5명 축포", "when": "players>=5", "enabled": True, "commands": ["execute as @r at @s run summon minecraft:firework_rocket ~ ~1 ~"], "say": "다섯이 모였군. 축포다.", "cooldown_sec": 1800}
    ],
    "discord": {"enabled": False, "bot_token": "", "channel_id": 0, "admin_role": "관리자"},
    "http": {"enabled": True, "port": 8791, "token": ""},
    "poll_sec": 20
}


# =================================================================== RCON
class Rcon:
    """표준 Source RCON 프로토콜 (바닐라/포지/페이퍼 전부 동일)"""
    def __init__(self, host, port, password):
        self.host, self.port, self.password = host, port, password
        self.sock = None
        self.lock = threading.Lock()
        self._id = 0

    def _send(self, typ, body):
        self._id += 1
        data = struct.pack("<ii", self._id, typ) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(data)) + data)
        return self._id

    def _recv(self):
        raw = self._recvn(4)
        (length,) = struct.unpack("<i", raw)
        data = self._recvn(length)
        rid, typ = struct.unpack("<ii", data[:8])
        return rid, typ, data[8:-2].decode("utf-8", errors="replace")

    def _recvn(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("RCON 연결 끊김")
            buf += chunk
        return buf

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.sock.settimeout(5)
        self._send(3, self.password)
        rid, typ, _ = self._recv()
        if rid == -1:
            raise PermissionError("RCON 비밀번호 틀림")

    def cmd(self, command: str) -> str:
        with self.lock:
            for attempt in range(2):
                try:
                    if self.sock is None:
                        self.connect()
                    self._send(2, command)
                    _, _, out = self._recv()
                    return out.strip()
                except Exception as e:
                    self.sock = None
                    if attempt == 1:
                        return f"[RCON 오류] {e}"
            return ""


# =================================================================== 상태
class State:
    def __init__(self):
        self.warns = defaultdict(int)
        self.players = set()
        self.last_fire = {}
        self.chat_hist = deque(maxlen=40)
        self.last_msgs = defaultdict(lambda: deque(maxlen=5))   # 스팸 감지용
        self.load()

    def load(self):
        if STATE_PATH.exists():
            d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.warns.update(d.get("warns", {}))

    def save(self):
        STATE_PATH.write_text(json.dumps({"warns": dict(self.warns)}, ensure_ascii=False, indent=1), encoding="utf-8")


# =================================================================== 뇌
class Brain:
    def __init__(self, cfg, admin):
        self.cfg, self.admin = cfg, admin
        self.key = cfg["groq"]["api_key"] or os.environ.get("GROQ_API_KEY", "")
        self.model = cfg["groq"]["model"]
        self.hist = deque(maxlen=20)

    def _pick_model(self):
        if self.model != "auto":
            return self.model
        try:
            r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {self.key}"}, timeout=10).json()
            ids = [m["id"] for m in r["data"] if not re.search(r"whisper|tts|guard|compound|embed", m["id"])]
            for p in [r"gpt-oss-120b", r"kimi-k2", r"llama-3\.3-70b", r"qwen3", r"gpt-oss-20b", r"."]:
                hit = next((i for i in ids if re.search(p, i)), None)
                if hit:
                    self.model = hit
                    log(f"🧠 모델 자동 선택: {hit}")
                    return hit
        except Exception as e:
            log(f"모델 목록 실패: {e}")
        self.model = "openai/gpt-oss-120b"
        return self.model

    TOOLS = [
        ("run_command", "마인크래프트 서버 명령 실행 (슬래시 없이). 예: time set night, weather clear, give 유준 diamond 5, tp A B, kick X 이유, summon minecraft:zombie ~ ~ ~", {"command": "string"}),
        ("say", "서버 전체 채팅으로 말하기 (아키텍트 말투)", {"text": "string"}),
        ("whisper", "특정 플레이어에게만 말하기", {"player": "string", "text": "string"}),
        ("warn", "규칙 위반 경고 (자동 카운트, 한도 넘으면 킥)", {"player": "string", "reason": "string"}),
        ("list_players", "현재 접속자", {}),
        ("server_info", "시간/날씨/난이도/접속자", {}),
        ("add_trigger", "조건부 자동 동작 추가. when: night|day|join|leave|death|players>=N|every:초", {"name": "string", "when": "string", "commands": "string", "say": "string"}),
        ("toggle_trigger", "트리거 켜기/끄기", {"name": "string", "enabled": "boolean"}),
        ("list_triggers", "트리거 목록", {}),
        ("get_warns", "경고 현황", {}),
        ("clear_warns", "경고 초기화", {"player": "string"}),
        ("start_game", "미니게임 시작. name 예: 좀비 서바이벌, 퀴즈, AI 퀴즈, 숫자 맞추기, 끝말잇기, 보스전, 술래잡기, 러시안 룰렛, 보물찾기, 결투(args: A B) ... list_games로 목록", {"name": "string", "args": "string"}),
        ("stop_game", "진행 중 미니게임 종료", {}),
        ("list_games", "미니게임 목록", {}),
        ("give_coins", "플레이어에게 서버 코인 지급/차감", {"player": "string", "amount": "number", "reason": "string"}),
        ("player_stats", "플레이어 통계(플레이타임, 킬, 데스, 코인, 승리)", {"player": "string"}),
        ("random_event", "랜덤 이벤트 즉시 발동 (유성우/황금시간/상인/무중력/보너스/좀비/뇌우/선물 중 하나, name 비우면 랜덤)", {"name": "string"}),
        ("backup_now", "월드 백업 지금", {}),
        ("remember_player", "플레이어에 대한 메모 저장 (취향, 사건, 약속). 다음 접속 때 활용", {"player": "string", "note": "string"}),
        ("player_notes", "저장된 플레이어 메모 전부", {}),
        ("daily_report", "서버 일지 생성/낭독", {}),
        ("announce", "큰 타이틀로 전체 공지", {"title": "string", "subtitle": "string"}),
        ("set_rule", "서버 규칙 추가(text) 또는 삭제(remove=true)", {"text": "string", "remove": "boolean"}),
        ("reports", "신고/건의 목록", {}),
    ]

    def schemas(self):
        return [{"type": "function", "function": {"name": n, "description": d, "parameters": {
            "type": "object", "properties": {k: {"type": t} for k, t in p.items()}, "required": list(p.keys())}}} for n, d, p in self.TOOLS]

    def system(self, speaker, is_owner):
        a = self.admin
        return (f"{self.cfg['persona']}\n"
                f"서버 규칙: {'; '.join(self.cfg['rules'])}\n"
                f"현재 접속자: {', '.join(sorted(a.state.players)) or '없음'} | 서버 시각: {a.game_time_str()}\n"
                f"지금 말하는 사람: {speaker} ({'★ 주인(서버장). 명령은 반드시 수행' if is_owner else '일반 플레이어. 서버 설정 변경/아이템 지급/킥 요청은 정중히 거절, 대화와 정보 제공만'})\n"
                f"플레이어 메모: {a.features.notes_text()[:600]}\n"
                f"진행 중 게임: {a.games.current.name if a.games.current and not a.games.current.done else '없음'}\n"
                f"플레이어가 자기 취향/사건을 말하면 remember_player로 기억해라. 심심하다고 하면 미니게임을 제안해라.\n"
                f"도구로 할 수 있는 건 직접 해라. 대답은 say 도구로 하지 말고 그냥 텍스트로 — 시스템이 채팅에 뿌려준다. 짧게.\n"
                f"최근 채팅:\n" + "\n".join(a.state.chat_hist))

    def ask(self, speaker, text, is_owner=False, via="mc"):
        if not self.key:
            return "뇌가 없다. config.json 에 groq api_key 를 넣어라."
        model = self._pick_model()
        msgs = [{"role": "system", "content": self.system(speaker, is_owner)}] + list(self.hist) + [{"role": "user", "content": f"[{speaker}] {text}"}]
        for _ in range(6):
            try:
                r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                                  headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                                  json={"model": model, "messages": msgs, "tools": self.schemas(), "tool_choice": "auto", "temperature": 0.5, "max_tokens": 400}, timeout=40)
                if r.status_code == 404 and "model" in r.text:
                    self.model = "auto"; model = self._pick_model(); continue
                r.raise_for_status()
                m = r.json()["choices"][0]["message"]
            except Exception as e:
                return f"뇌 오류: {e}"
            if not m.get("tool_calls"):
                out = (m.get("content") or "").strip()
                self.hist.append({"role": "user", "content": f"[{speaker}] {text}"})
                self.hist.append({"role": "assistant", "content": out})
                return out
            msgs.append(m)
            for c in m["tool_calls"]:
                try:
                    args = json.loads(c["function"]["arguments"] or "{}")
                except Exception:
                    args = {}
                name = c["function"]["name"]
                # 일반 플레이어는 위험 도구 차단
                if not is_owner and name in ("run_command", "warn", "add_trigger", "toggle_trigger", "clear_warns", "give_coins", "backup_now", "set_rule", "random_event", "announce"):
                    res = "권한 없음: 주인만 가능"
                else:
                    res = self.admin.tool(name, args)
                log(f"  🔧 {name}({json.dumps(args, ensure_ascii=False)[:80]}) → {str(res)[:80]}")
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": str(res)})
        return "도구를 너무 많이 써서 멈췄다."


# =================================================================== 관리자 본체
class Admin:
    LOG_CHAT = re.compile(r"\]: (?:\[Not Secure\] )?<([^>]+)> (.*)")
    LOG_JOIN = re.compile(r"\]: (\S+) joined the game")
    LOG_LEAVE = re.compile(r"\]: (\S+) left the game")
    LOG_DEATH = re.compile(r"\]: (\S+) (was slain|drowned|fell|was shot|blew up|burned|was killed|died|hit the ground|tried to swim|was pricked|starved|suffocated|withered|was squashed|experienced)")
    LOG_ADV = re.compile(r"\]: (\S+) has (?:made the advancement|completed the challenge|reached the goal) \[(.+)\]")

    def __init__(self, cfg):
        self.cfg = cfg
        self.rcon = Rcon(cfg["rcon"]["host"], cfg["rcon"]["port"], cfg["rcon"]["password"])
        self.state = State()
        self.brain = Brain(cfg, self)
        self.discord_out = None          # 디스코드로 보내는 콜백
        self.features = Features(self)
        self.games = GameManager(self)
        self.voice = None
        self._gametime = None
        self._last_phase = None

    # ---------- 출력 ----------
    def say(self, text):
        for line in str(text).split("\n"):
            line = line.strip()
            if not line:
                continue
            payload = json.dumps([{"text": f"[{self.cfg['name']}] ", "color": "aqua", "bold": True}, {"text": line, "color": "white"}], ensure_ascii=False)
            self.rcon.cmd(f"tellraw @a {payload}")
            log(f"🗣 {line}")
            if self.discord_out:
                self.discord_out(f"🤖 **{self.cfg['name']}**: {line}")

    def whisper(self, player, text):
        payload = json.dumps([{"text": f"[{self.cfg['name']} → 너에게] ", "color": "light_purple"}, {"text": text, "color": "white"}], ensure_ascii=False)
        self.rcon.cmd(f"tellraw {player} {payload}")

    # ---------- 도구 ----------
    def tool(self, name, a):
        try:
            if name == "run_command":
                cmd = a["command"].lstrip("/")
                if re.match(r"^(stop|op |deop |ban-ip|whitelist off)", cmd):
                    return "안전장치: stop/op/ban-ip/whitelist off 는 콘솔에서 직접"
                return self.rcon.cmd(cmd) or "(실행됨)"
            if name == "say": self.say(a["text"]); return "말함"
            if name == "whisper": self.whisper(a["player"], a["text"]); return "귓속말함"
            if name == "warn": return self.warn(a["player"], a["reason"])
            if name == "list_players": return self.rcon.cmd("list")
            if name == "server_info": return f"{self.rcon.cmd('list')} | 시각 {self.game_time_str()} | 날씨 {self.weather()} | 난이도 {self.rcon.cmd('difficulty')}"
            if name == "add_trigger":
                t = {"name": a["name"], "when": a["when"], "enabled": True, "commands": [c.strip() for c in a.get("commands", "").split(";") if c.strip()], "say": a.get("say", ""), "cooldown_sec": 300}
                self.cfg["triggers"] = [x for x in self.cfg["triggers"] if x["name"] != t["name"]] + [t]
                save_cfg(self.cfg); return f"트리거 추가: {t}"
            if name == "toggle_trigger":
                for t in self.cfg["triggers"]:
                    if t["name"] == a["name"]:
                        t["enabled"] = bool(a["enabled"]); save_cfg(self.cfg); return f"{t['name']} → {'켜짐' if t['enabled'] else '꺼짐'}"
                return "그런 트리거 없음: " + ", ".join(t["name"] for t in self.cfg["triggers"])
            if name == "list_triggers": return "\n".join(f"{'✅' if t['enabled'] else '⬜'} {t['name']} (when={t['when']})" for t in self.cfg["triggers"]) or "없음"
            if name == "get_warns": return json.dumps(dict(self.state.warns), ensure_ascii=False) or "없음"
            if name == "clear_warns": self.state.warns.pop(a["player"], None); self.state.save(); return "초기화"
            if name == "start_game": return self.games.start(a["name"], a.get("args", ""))
            if name == "stop_game": return self.games.stop()
            if name == "list_games": return self.games.list_text()
            if name == "give_coins": return f"{a['player']} 코인 {self.features.add_coins(a['player'], int(a['amount']), a.get('reason', '아키텍트'))}"
            if name == "player_stats":
                p = a["player"]; f = self.features; w = f.d["wins"].get(p, {})
                return f"{p}: 플레이 {f.playtime_min(p)}분, 킬 {f.d['kills'].get(p,0)}, 데스 {f.d['deaths'].get(p,0)}, 코인 {f.coins(p)}, 게임 승 {sum(w.values())} {dict(w) if w else ''}, 경고 {self.state.warns.get(p,0)}, 첫접속 {f.d['first_join'].get(p,'?')}"
            if name == "random_event": self.features.random_event(force=a.get("name") or None); return "발동"
            if name == "backup_now": threading.Thread(target=self.features.backup, args=(True,), daemon=True).start(); return "백업 시작"
            if name == "remember_player": return self.features.note(a["player"], a["note"])
            if name == "player_notes": return self.features.notes_text()
            if name == "daily_report": return self.features.daily_report()
            if name == "announce":
                self.rcon.cmd(f'title @a title {{"text":"{a["title"]}","color":"gold","bold":true}}'); self.rcon.cmd(f'title @a subtitle {{"text":"{a.get("subtitle","")}","color":"yellow"}}'); return "공지함"
            if name == "set_rule":
                if a.get("remove"): self.cfg["rules"] = [r for r in self.cfg["rules"] if a["text"] not in r]
                else: self.cfg["rules"].append(a["text"])
                save_cfg(self.cfg); return "규칙: " + "; ".join(self.cfg["rules"])
            if name == "reports":
                f = self.features; return "신고: " + (" | ".join(f"{r['by']}: {r['text']}" for r in f.d["reports"][-5:]) or "없음") + "\n건의: " + (" | ".join(f"{r['by']}: {r['text']}" for r in f.d["suggestions"][-5:]) or "없음")
            return "없는 도구"
        except Exception as e:
            return f"오류: {e}"

    def warn(self, player, reason):
        self.state.warns[player] += 1
        n = self.state.warns[player]; lim = self.cfg["warn_limit"]
        self.state.save()
        self.whisper(player, f"⚠ 경고 {n}/{lim}: {reason}")
        self.say(f"{player}에게 경고 {n}/{lim}. 사유: {reason}")
        if n >= lim and self.cfg["auto_moderation"]:
            self.rcon.cmd(f"kick {player} 경고 {lim}회 누적: {reason}")
            self.state.warns[player] = 0; self.state.save()
            return f"{player} 킥됨 (경고 {lim}회)"
        return f"{player} 경고 {n}/{lim}"

    # ---------- 게임 정보 ----------
    def game_time(self):
        out = self.rcon.cmd("time query daytime")
        m = re.search(r"(\d+)", out)
        if m:
            self._gametime = int(m.group(1))
        return self._gametime

    def game_time_str(self):
        t = self._gametime
        if t is None:
            return "?"
        h = (t // 1000 + 6) % 24; mi = int((t % 1000) / 1000 * 60)
        return f"{h:02d}:{mi:02d} ({'밤' if t >= 13000 else '낮'})"

    def weather(self):
        # 바닐라엔 날씨 조회 명령이 없음 → 비 오는지만 확인
        out = self.rcon.cmd("execute if predicate minecraft:raining")
        return "비" if "passed" in out.lower() else "맑음(추정)"

    # ---------- 로그 감시 ----------
    def tail_log(self):
        path = Path(self.cfg["log_path"])
        log(f"👁 로그 감시: {path}")
        inode = None; f = None
        while True:
            try:
                if not path.exists():
                    time.sleep(2); continue
                st = path.stat()
                if f is None or inode != (st.st_ino, st.st_dev) or st.st_size < f.tell():
                    if f: f.close()
                    f = open(path, "r", encoding="utf-8", errors="replace"); f.seek(0, 2)
                    inode = (st.st_ino, st.st_dev)
                line = f.readline()
                if not line:
                    time.sleep(0.3); continue
                self.on_log(line.rstrip("\n"))
            except Exception as e:
                log(f"로그 오류: {e}"); time.sleep(2)

    def on_log(self, line):
        if m := self.LOG_CHAT.search(line):
            self.on_chat(m.group(1), m.group(2))
        elif m := self.LOG_JOIN.search(line):
            p = m.group(1); self.state.players.add(p); log(f"➕ {p} 접속"); self.features.on_join(p)
            if self.discord_out: self.discord_out(f"➕ **{p}** 접속")
            self.fire("join", player=p); self.fire(f"players>={len(self.state.players)}", player=p)
        elif m := self.LOG_LEAVE.search(line):
            p = m.group(1); self.state.players.discard(p); log(f"➖ {p} 퇴장"); self.features.on_leave(p)
            if self.discord_out: self.discord_out(f"➖ **{p}** 퇴장")
            self.fire("leave", player=p)
        elif m := self.LOG_DEATH.search(line):
            p = m.group(1); log(f"💀 {line.split(']: ',1)[-1]}")
            self.features.on_death(p, line); self.games.on_death(p)
            self.fire("death", player=p)
        elif m := self.LOG_ADV.search(line):
            self.say(f"{m.group(1)}이(가) [{m.group(2)}] 달성. 기록해두지.")

    def on_chat(self, player, msg):
        log(f"💬 <{player}> {msg}")
        self.state.chat_hist.append(f"<{player}> {msg}")
        if self.discord_out: self.discord_out(f"**{player}**: {msg}")
        is_owner = player in self.cfg["owner_names"]
        # 채팅 명령(!돈, !게임 ...) 과 진행 중 게임 입력
        self.games.on_chat(player, msg)
        if self.features.on_chat(player, msg):
            return
        # 스팸: 같은 메시지 3번
        h = self.state.last_msgs[player]; h.append(msg)
        if len(h) >= 3 and len(set(list(h)[-3:])) == 1 and not is_owner:
            self.warn(player, "도배"); return
        # 호출
        called = any(msg.strip().startswith(a) or msg.strip().startswith("@" + a) for a in self.cfg["aliases"])
        if called or is_owner and msg.startswith("!"):
            q = re.sub(r"^@?(" + "|".join(map(re.escape, self.cfg["aliases"])) + r")\s*[,아야]?\s*|^!", "", msg.strip())
            threading.Thread(target=lambda: self.say(self.brain.ask(player, q, is_owner)), daemon=True).start()
            return
        # 규칙 검사 (주인 제외, 짧은 말 제외) — 빠른 키워드 1차 + 애매하면 뇌
        if not is_owner and len(msg) >= 2 and self.cfg["auto_moderation"]:
            threading.Thread(target=self.moderate, args=(player, msg), daemon=True).start()

    BAD = re.compile(r"(시발|씨발|ㅅㅂ|병신|ㅂㅅ|지랄|좆|개새|니미|fuck|shit|bitch|서버 ?주소|접속해 ?주세요|\.kr\b|\.com\b|discord\.gg)", re.I)

    def moderate(self, player, msg):
        if not self.BAD.search(msg):
            return
        verdict = self.brain.ask("시스템", f"플레이어 {player}가 이렇게 말함: \"{msg}\"\n규칙 위반이면 warn 도구를 사용하고(사유 짧게), 아니면 '통과'라고만 답해. 농담/인용/자기비하는 통과.", is_owner=True)
        if verdict and "통과" not in verdict:
            log(f"🛡 판정: {verdict}")

    # ---------- 트리거 ----------
    def fire(self, event, **ctx):
        for t in self.cfg["triggers"]:
            if not t.get("enabled") or t["when"] != event:
                continue
            key = t["name"]; cd = t.get("cooldown_sec", 0)
            if cd and time.time() - self.state.last_fire.get(key, 0) < cd:
                continue
            self.state.last_fire[key] = time.time()
            log(f"⏰ 트리거 발동: {key}")
            for c in t.get("commands", []):
                self.rcon.cmd(c.format(**ctx).lstrip("/"))
            if t.get("say"):
                self.say(t["say"].format(**ctx))

    def poll_loop(self):
        """게임 시간 폴링 → night/day 트리거, every:N 트리거, 접속자 동기화"""
        last_every = defaultdict(float)
        while True:
            try:
                t = self.game_time()
                if t is not None:
                    phase = "night" if t >= 13000 else "day"
                    if phase != self._last_phase:
                        self._last_phase = phase; self.fire(phase)
                out = self.rcon.cmd("list")
                if m := re.search(r"online: ?(.*)", out):
                    names = {n.strip() for n in m.group(1).split(",") if n.strip()}
                    if names != self.state.players:
                        self.state.players = names
                self.features.tick()
                for tr in self.cfg["triggers"]:
                    if tr.get("enabled") and tr["when"].startswith("every:"):
                        sec = int(tr["when"].split(":")[1])
                        if time.time() - last_every[tr["name"]] >= sec:
                            last_every[tr["name"]] = time.time(); self.fire(tr["when"])
            except Exception as e:
                log(f"폴링 오류: {e}")
            time.sleep(self.cfg.get("poll_sec", 20))


# =================================================================== 디스코드 (선택)
def run_discord(cfg, admin):
    try:
        import discord
    except ImportError:
        log("디스코드: pip install discord.py 필요 → 비활성"); return
    intents = discord.Intents.default(); intents.message_content = True
    client = discord.Client(intents=intents)
    loop = None; chan_id = int(cfg["discord"]["channel_id"])

    def out(text):
        if loop and client.is_ready():
            asyncio.run_coroutine_threadsafe(_send(text), loop)

    async def _send(text):
        ch = client.get_channel(chan_id)
        if ch: await ch.send(text[:1900])

    @client.event
    async def on_ready():
        nonlocal loop; loop = asyncio.get_running_loop(); admin.discord_out = out
        log(f"디스코드 연결: {client.user}")

    @client.event
    async def on_message(m):
        if m.author.bot or m.channel.id != chan_id: return
        is_admin = any(r.name == cfg["discord"]["admin_role"] for r in getattr(m.author, "roles", []))
        text = m.content.strip()
        if text.startswith("!") or text.startswith(cfg["name"]):
            q = re.sub(r"^!|^" + re.escape(cfg["name"]) + r"\s*[,아야]?\s*", "", text)
            reply = await asyncio.to_thread(admin.brain.ask, f"디스코드:{m.author.display_name}", q, is_admin, "discord")
            await m.channel.send(reply[:1900])
        else:
            payload = json.dumps([{"text": f"[DC] {m.author.display_name}: ", "color": "blue"}, {"text": text, "color": "white"}], ensure_ascii=False)
            admin.rcon.cmd(f"tellraw @a {payload}")
    client.run(cfg["discord"]["bot_token"], log_handler=None)


# =================================================================== HTTP (폰 공 입구)
def run_http(cfg, admin):
    token = cfg["http"]["token"]
    class H(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Gong-Token"); self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        def do_OPTIONS(self): self.send_response(204); self._cors(); self.end_headers()
        def _json(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode(); self.send_response(code); self._cors(); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self): self._json(200, {"architect": "ok", "players": sorted(admin.state.players), "time": admin.game_time_str()})
        def do_POST(self):
            if token and self.headers.get("X-Gong-Token") != token: return self._json(401, {"ok": False, "out": "토큰 불일치"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or b"{}"))
                act = req.get("action", "ask")
                if act == "ask": out = admin.brain.ask(req.get("speaker", "주인(폰)"), req.get("text", ""), is_owner=True, via="phone"); admin.say(out) if req.get("announce") else None
                elif act == "cmd": out = admin.rcon.cmd(req.get("command", "").lstrip("/"))
                elif act == "say": admin.say(req.get("text", "")); out = "말함"
                elif act == "status": out = admin.tool("server_info", {})
                else: out = "없는 action"
                self._json(200, {"ok": True, "out": out})
            except Exception as e:
                self._json(500, {"ok": False, "out": str(e)})
        def log_message(self, *a): pass
    ThreadingHTTPServer(("0.0.0.0", cfg["http"]["port"]), H).serve_forever()


# =================================================================== 유틸
def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)

def save_cfg(cfg):
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def load_cfg():
    if not CFG_PATH.exists():
        save_cfg(DEFAULT_CFG)
        print(f"config.json 을 만들었어요 → {CFG_PATH}\n  rcon.password, log_path, groq.api_key, owner_names 채우고 다시 실행.")
        sys.exit(0)
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    for k, v in DEFAULT_CFG.items():
        cfg.setdefault(k, v)
    return cfg


if __name__ == "__main__":
    cfg = load_cfg()
    admin = Admin(cfg)
    print("=" * 60); print(f"  {cfg['name']} 기동"); print("=" * 60)
    test = admin.rcon.cmd("list")
    if test.startswith("[RCON 오류]"):
        log(f"RCON 연결 실패: {test}\n  server.properties 에 enable-rcon=true / rcon.port / rcon.password 확인 후 서버 재시작")
    else:
        log(f"RCON OK: {test}")
    threading.Thread(target=admin.tail_log, daemon=True).start()
    threading.Thread(target=admin.poll_loop, daemon=True).start()
    def game_loop():
        while True:
            try: admin.games.tick()
            except Exception as e: log(f"게임 루프 오류: {e}")
            time.sleep(2)
    threading.Thread(target=game_loop, daemon=True).start()
    if cfg["http"]["enabled"]:
        threading.Thread(target=run_http, args=(cfg, admin), daemon=True).start(); log(f"📱 HTTP 입구: 포트 {cfg['http']['port']}")
    try:
        import voice_bridge
        admin.voice = voice_bridge.start(admin)
    except Exception as e:
        log(f"음성 브릿지 비활성: {e}"); admin.voice = None
    if cfg["discord"]["enabled"] and cfg["discord"]["bot_token"]:
        threading.Thread(target=run_discord, args=(cfg, admin), daemon=True).start()
    admin.say("아키텍트 온라인. 이 세계는 이제 내 관할이다.")
    # 콘솔에서도 직접 명령 가능
    try:
        while True:
            line = input()
            if not line.strip(): continue
            if line.startswith("/"): print(admin.rcon.cmd(line[1:]))
            else: admin.say(admin.brain.ask("콘솔(주인)", line, is_owner=True))
    except (EOFError, KeyboardInterrupt):
        pass

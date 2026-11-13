"""
아키텍트 서버 기능 팩 — 채팅 명령 + 자동 기능
채팅 명령 (누구나):  !도움  !돈  !송금 이름 금액  !상점  !구매 번호  !랭킹  !집 저장 / !집  !신고 이름 이유  !건의 내용
                      !좌표  !시간  !날씨투표 / !시간투표  !칭호 글자  !통계  !데스  !출석  !룰  !게임 ...
주인/운영진 전용:     !백업  !공지 내용  !이벤트  !afk 켜/꺼  !운영진 이름  !리셋돈 이름  !일지
자동:  플레이타임, 첫 접속 선물, 출석 보상, AFK 감지, 자동 백업, 공지 로테이션, 랜덤 이벤트, 일일 리포트, 데스 좌표 안내,
       플레이어 메모(AI 기억), 킬/데스 통계, 캡스락·도배 감지(기존 규칙엔진과 연동)
"""
import json
import os
import random
import re
import shutil
import threading
import time
import zipfile
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data.json"

SHOP = [  # (이름, 가격, 명령 {p}=플레이어)
    ("다이아몬드 1개", 60, "give {p} minecraft:diamond 1"), ("황금사과 1개", 40, "give {p} minecraft:golden_apple 1"),
    ("엔더진주 4개", 50, "give {p} minecraft:ender_pearl 4"), ("철 괴 16개", 30, "give {p} minecraft:iron_ingot 16"),
    ("경험치 30레벨", 80, "xp add {p} 30 levels"), ("속도 버프 10분", 25, "effect give {p} minecraft:speed 600 1 true"),
    ("야간투시 10분", 20, "effect give {p} minecraft:night_vision 600 0 true"), ("스폰으로 귀환", 10, "tp {p} 0 ~ 0"),
    ("네더라이트 조각 1개", 150, "give {p} minecraft:netherite_scrap 1"), ("불꽃놀이 쇼", 15, "execute at {p} run summon minecraft:firework_rocket ~ ~1 ~ {{LifeTime:30,FireworksItem:{{id:firework_rocket,Count:1,tag:{{Fireworks:{{Explosions:[{{Type:1,Colors:[I;16711680,65280,255]}}]}}}}}}}}"),
]
RANDOM_EVENTS = [
    ("☄ 유성우", ["execute as @a at @s run summon minecraft:fireball ~{dx} ~25 ~{dz} {{power:[0.0,-1.0,0.0],ExplosionPower:1}}"] * 5, "하늘을 봐라. 유성이 떨어진다."),
    ("✨ 황금 시간", ["effect give @a minecraft:luck 300 2 true", "xp add @a 10 levels"], "5분간 행운. 경험치 10레벨 선물."),
    ("🧙 떠돌이 상인", ["execute as @r at @s run summon minecraft:wandering_trader ~3 ~ ~3"], "상인이 누군가의 곁에 나타났다."),
    ("🌙 무중력 1분", ["effect give @a minecraft:jump_boost 60 5 true", "effect give @a minecraft:slow_falling 60 0 true"], "중력이 약해진다."),
    ("💰 보너스", [], "전원 코인 +20."),
    ("🧟 좀비 습격", ["execute as @a at @s run summon minecraft:zombie ~6 ~ ~6", "execute as @a at @s run summon minecraft:zombie ~-6 ~ ~6", "execute as @a at @s run summon minecraft:zombie ~6 ~ ~-6"], "좀비 떼가 몰려온다. 버텨라."),
    ("⚡ 뇌우", ["weather thunder 600"], "폭풍이 온다."),
    ("🎁 하늘 선물", ["execute as @a at @s run summon minecraft:chicken ~ ~15 ~ {{Passengers:[{{id:\"minecraft:item\",Item:{{id:\"minecraft:golden_apple\",Count:1b}}}}]}}"], "머리 위를 봐라."),
]
TITLES_FREE = ["초보", "모험가", "광부", "건축가", "탐험가", "농부", "낚시꾼", "전사"]


class Features:
    def __init__(self, admin):
        self.a = admin; self.cfg = admin.cfg
        self.d = {"coins": {}, "playtime": {}, "sessions": {}, "first_join": {}, "attend": {}, "homes": {}, "reports": [], "suggestions": [],
                  "kills": {}, "deaths": {}, "wins": {}, "titles": {}, "notes": {}, "mods": [], "banned_words": [], "journal": []}
        self.load()
        self.f = self.cfg.setdefault("features", {})
        for k, v in {"afk_minutes": 10, "afk_action": "announce", "backup_every_min": 60, "backup_keep": 10, "world_dir": "",
                     "announce_every_min": 15, "announcements": ["!도움 으로 명령어 확인", "!게임 목록 으로 미니게임", "!출석 으로 매일 코인"],
                     "random_event_every_min": 40, "daily_report_hour": 22, "attend_reward": 20, "first_join_gift": True,
                     "death_coords": True, "coins_per_minute": 1, "caps_warn": True}.items():
            self.f.setdefault(k, v)
        self.last_pos = {}; self.afk = set(); self.last_backup = time.time(); self.last_announce = time.time(); self.ann_i = 0
        self.last_event = time.time(); self.last_report_day = None; self.vote = None

    # ---------- 저장 ----------
    def load(self):
        if DATA.exists():
            try: self.d.update(json.loads(DATA.read_text(encoding="utf-8")))
            except Exception: pass
    def save(self):
        try: DATA.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e: print("save err", e)

    # ---------- 권한 ----------
    def is_owner(self, p): return p in self.cfg["owner_names"]
    def is_mod(self, p): return self.is_owner(p) or p in self.d["mods"]

    # ---------- 경제 ----------
    def coins(self, p): return self.d["coins"].get(p, 0)
    def add_coins(self, p, n, why=""):
        self.d["coins"][p] = self.coins(p) + n; self.save()
        if why and n > 0: self.a.whisper(p, f"💰 +{n} 코인 ({why}) → {self.coins(p)}")
        return self.coins(p)

    # ---------- 통계 ----------
    def record_win(self, p, game):
        self.d["wins"].setdefault(p, {}); self.d["wins"][p][game] = self.d["wins"][p].get(game, 0) + 1; self.save()
    def playtime_min(self, p): return int(self.d["playtime"].get(p, 0) + ((time.time() - self.d["sessions"][p]) / 60 if p in self.d["sessions"] else 0))

    # ---------- 이벤트 훅 ----------
    def on_join(self, p):
        self.d["sessions"][p] = time.time()
        today = str(date.today())
        if p not in self.d["first_join"]:
            self.d["first_join"][p] = today
            if self.f["first_join_gift"]:
                self.a.rcon.cmd(f"give {p} minecraft:bread 16"); self.a.rcon.cmd(f"give {p} minecraft:stone_sword"); self.a.rcon.cmd(f"give {p} minecraft:torch 16")
                self.add_coins(p, 50, "첫 접속 선물"); self.a.say(f"🎉 {p}, 이 세계에 처음 왔군. 시작 물자를 넣어뒀다. !도움 을 쳐봐라.")
        else:
            note = self.d["notes"].get(p)
            self.a.whisper(p, f"다시 왔군, {p}. 플레이타임 {self.playtime_min(p)}분, 코인 {self.coins(p)}." + (f" ({note[-1]})" if note else ""))
        self.save()

    def on_leave(self, p):
        if p in self.d["sessions"]:
            self.d["playtime"][p] = self.d["playtime"].get(p, 0) + (time.time() - self.d["sessions"].pop(p)) / 60
        self.afk.discard(p); self.last_pos.pop(p, None); self.save()

    def on_death(self, p, line):
        self.d["deaths"][p] = self.d["deaths"].get(p, 0) + 1
        km = re.search(r"slain by (\S+)|shot by (\S+)|killed by (\S+)", line)
        if km:
            k = next(x for x in km.groups() if x)
            if k in self.a.state.players: self.d["kills"][k] = self.d["kills"].get(k, 0) + 1
        self.save()
        if self.f["death_coords"]:
            def later():
                time.sleep(4)
                out = self.a.rcon.cmd(f"data get entity {p} LastDeathLocation.pos")
                m = re.findall(r"-?\d+", out)
                if len(m) >= 3: self.a.whisper(p, f"💀 죽은 위치: x={m[0]} y={m[1]} z={m[2]}")
            threading.Thread(target=later, daemon=True).start()

    def on_chat(self, p, msg):
        """채팅 명령 처리. True 반환 = 처리됨(뇌로 안 넘김)"""
        if self.f["caps_warn"] and len(msg) >= 8 and sum(c.isupper() for c in msg) / max(1, sum(c.isalpha() for c in msg)) > 0.8 and not self.is_mod(p):
            self.a.whisper(p, "🔇 대문자 도배는 자제.")
        if self.vote and msg.strip() in ("1", "2", "찬성", "반대"):
            self.vote["votes"][p] = msg.strip() in ("1", "찬성"); return True
        if not msg.startswith("!"): return False
        parts = msg[1:].split(); c = parts[0] if parts else ""; arg = " ".join(parts[1:])
        W = lambda t: self.a.whisper(p, t)
        S = self.a.say
        try:
            if c in ("도움", "help", "명령어"):
                W("!돈 !송금 !상점 !구매 !랭킹 !집 !신고 !건의 !좌표 !시간 !날씨투표 !시간투표 !칭호 !통계 !출석 !룰 !게임 목록 !암호문 !따라해 !음성" + (" | 운영: !백업 !공지 !이벤트 !afk !운영진 !일지" if self.is_mod(p) else "")); return True
            if c in ("돈", "코인", "잔액"): W(f"💰 {self.coins(p)} 코인"); return True
            if c == "송금":
                to, n = parts[1], int(parts[2])
                if n <= 0 or self.coins(p) < n: W("잔액 부족"); return True
                self.add_coins(p, -n); self.add_coins(to, n, f"{p}의 송금"); W(f"{to}에게 {n} 송금"); return True
            if c == "상점": W("🛒 " + " / ".join(f"{i+1}.{n} {pr}c" for i, (n, pr, _) in enumerate(SHOP)) + "  → !구매 번호"); return True
            if c == "구매":
                i = int(parts[1]) - 1; n, pr, cmd = SHOP[i]
                if self.coins(p) < pr: W(f"부족: {pr} 코인 필요 (보유 {self.coins(p)})"); return True
                self.add_coins(p, -pr); self.a.rcon.cmd(cmd.format(p=p)); S(f"🛒 {p}가 '{n}' 구매."); return True
            if c in ("랭킹", "순위"):
                top = lambda d: ", ".join(f"{k} {int(v) if not isinstance(v, dict) else sum(v.values())}" for k, v in sorted(d.items(), key=lambda x: -(x[1] if not isinstance(x[1], dict) else sum(x[1].values())))[:5])
                pt = {k: self.playtime_min(k) for k in set(self.d["playtime"]) | set(self.d["sessions"])}
                S(f"🏆 코인: {top(self.d['coins'])}\n⏱ 플레이(분): {top(pt)}\n⚔ 킬: {top(self.d['kills'])}\n🎮 승리: {top(self.d['wins'])}"); return True
            if c == "집":
                if arg == "저장":
                    out = self.a.rcon.cmd(f"data get entity {p} Pos"); m = re.findall(r"(-?\d+)\.\d+d", out)
                    if len(m) == 3: self.d["homes"][p] = m; self.save(); W(f"🏠 집 저장: {' '.join(m)}")
                    else: W("위치를 못 읽음")
                elif p in self.d["homes"]: self.a.rcon.cmd(f"tp {p} {' '.join(self.d['homes'][p])}"); W("🏠 집으로")
                else: W("저장된 집 없음. !집 저장")
                return True
            if c == "좌표":
                out = self.a.rcon.cmd(f"data get entity {p} Pos"); m = re.findall(r"(-?\d+)\.\d+d", out); W(f"📍 {' '.join(m) if m else '?'}"); return True
            if c == "시간": W(f"🕒 서버 {self.a.game_time_str()} / 현실 {datetime.now():%H:%M}"); return True
            if c == "신고":
                self.d["reports"].append({"by": p, "text": arg, "at": str(datetime.now())[:16]}); self.save(); W("🚨 접수됨. 운영진이 확인한다.")
                if self.a.discord_out: self.a.discord_out(f"🚨 신고 by {p}: {arg}")
                return True
            if c == "건의": self.d["suggestions"].append({"by": p, "text": arg, "at": str(datetime.now())[:16]}); self.save(); W("📮 건의 접수. 고맙다."); return True
            if c in ("날씨투표", "시간투표"):
                if self.vote: W("이미 투표 중"); return True
                what = "weather" if c == "날씨투표" else "time"
                self.vote = {"what": what, "votes": {}, "t": time.time()}; S(f"📊 {'비 그치기' if what == 'weather' else '낮으로 바꾸기'} 투표. 1=찬성 2=반대 (30초)")
                def close():
                    time.sleep(30); v = self.vote; self.vote = None
                    yes = sum(v["votes"].values()); no = len(v["votes"]) - yes
                    if yes > no: self.a.rcon.cmd("weather clear" if what == "weather" else "time set day"); S(f"📊 가결 {yes}:{no}. 실행.")
                    else: S(f"📊 부결 {yes}:{no}.")
                threading.Thread(target=close, daemon=True).start(); return True
            if c == "칭호":
                t = arg.strip()[:8]
                if not t: W("예: !칭호 광부 (무료: " + ", ".join(TITLES_FREE) + " / 커스텀 100코인)"); return True
                if t not in TITLES_FREE:
                    if self.coins(p) < 100: W("커스텀 칭호는 100 코인"); return True
                    self.add_coins(p, -100)
                self.d["titles"][p] = t; self.save()
                self.a.rcon.cmd(f"team add t_{p}"); self.a.rcon.cmd(f'team modify t_{p} prefix {{"text":"[{t}] ","color":"gold"}}'); self.a.rcon.cmd(f"team join t_{p} {p}")
                S(f"🏷 {p} → [{t}]"); return True
            if c in ("통계", "스탯"):
                w = self.d["wins"].get(p, {}); W(f"📈 플레이 {self.playtime_min(p)}분 | 킬 {self.d['kills'].get(p,0)} | 데스 {self.d['deaths'].get(p,0)} | 코인 {self.coins(p)} | 게임 승 {sum(w.values())} | 첫 접속 {self.d['first_join'].get(p,'?')}"); return True
            if c == "출석":
                today = str(date.today())
                if self.d["attend"].get(p) == today: W("오늘 이미 출석"); return True
                self.d["attend"][p] = today; self.add_coins(p, self.f["attend_reward"], "출석"); return True
            if c in ("룰", "규칙"): W("📜 " + " / ".join(self.cfg["rules"])); return True
            if c == "데스": W(f"💀 데스 {self.d['deaths'].get(p,0)}회"); return True
            if c in ("암호문", "따라해", "음성") and getattr(self.a, "voice", None):
                r = self.a.voice.on_chat(p, c, arg)
                if r: W(r); return True
            if c == "게임":
                if not arg or arg == "목록": S(self.a.games.list_text()); return True
                if arg in ("종료", "중지", "stop"): S(self.a.games.stop()); return True
                name, _, rest = arg.partition(" "); S(self.a.games.start(name, rest)); return True
            # ---- 운영진 ----
            if self.is_mod(p):
                if c == "백업": threading.Thread(target=self.backup, args=(True,), daemon=True).start(); return True
                if c == "공지": S(f"📢 {arg}"); return True
                if c == "이벤트": self.random_event(force=arg or None); return True
                if c == "afk": self.f["afk_action"] = "kick" if "킥" in arg else ("off" if "꺼" in arg else "announce"); W(f"AFK: {self.f['afk_action']}"); return True
                if c == "운영진":
                    if arg in self.d["mods"]: self.d["mods"].remove(arg); W(f"{arg} 운영진 해제")
                    else: self.d["mods"].append(arg); W(f"{arg} 운영진 임명")
                    self.save(); return True
                if c == "리셋돈": self.d["coins"][arg] = 0; self.save(); W("초기화"); return True
                if c == "일지": S(self.daily_report(announce=False)); return True
                if c == "신고목록": W(" | ".join(f"{r['at']} {r['by']}: {r['text']}" for r in self.d["reports"][-5:]) or "없음"); return True
                if c == "건의목록": W(" | ".join(f"{r['at']} {r['by']}: {r['text']}" for r in self.d["suggestions"][-5:]) or "없음"); return True
        except (IndexError, ValueError) as e:
            W(f"사용법 오류 (!도움)"); return True
        return False

    # ---------- 주기 작업 (poll_loop에서 20초마다) ----------
    def tick(self):
        now = time.time()
        # 플레이타임 코인
        if int(now) % 60 < 20:
            for p in self.a.state.players:
                if p not in self.afk: self.d["coins"][p] = self.coins(p) + self.f["coins_per_minute"]
        # AFK
        if self.f["afk_action"] != "off":
            for p in list(self.a.state.players):
                out = self.a.rcon.cmd(f"data get entity {p} Pos"); m = re.findall(r"(-?\d+)\.\d+d", out)
                pos = tuple(m) if len(m) == 3 else None
                lp = self.last_pos.get(p)
                if lp and lp[0] == pos:
                    if now - lp[1] > self.f["afk_minutes"] * 60 and p not in self.afk:
                        self.afk.add(p)
                        if self.f["afk_action"] == "kick": self.a.rcon.cmd(f"kick {p} {self.f['afk_minutes']}분 이상 AFK")
                        else: self.a.say(f"💤 {p} 잠수 중.")
                else:
                    self.last_pos[p] = (pos, now)
                    if p in self.afk: self.afk.discard(p); self.a.say(f"👋 {p} 복귀.")
        # 자동 백업
        if self.f["backup_every_min"] and now - self.last_backup > self.f["backup_every_min"] * 60: threading.Thread(target=self.backup, daemon=True).start()
        # 공지 로테이션
        if self.f["announce_every_min"] and self.a.state.players and now - self.last_announce > self.f["announce_every_min"] * 60:
            self.last_announce = now; anns = self.f["announcements"]
            if anns: self.a.say("📢 " + anns[self.ann_i % len(anns)]); self.ann_i += 1
        # 랜덤 이벤트
        if self.f["random_event_every_min"] and len(self.a.state.players) >= 1 and now - self.last_event > self.f["random_event_every_min"] * 60:
            self.random_event()
        # 일일 리포트
        h = datetime.now().hour
        if h == self.f["daily_report_hour"] and self.last_report_day != str(date.today()):
            self.last_report_day = str(date.today()); self.daily_report()
        self.save()

    def random_event(self, force=None):
        self.last_event = time.time()
        ev = next((e for e in RANDOM_EVENTS if force and force in e[0]), None) or random.choice(RANDOM_EVENTS)
        name, cmds, msg = ev
        self.a.say(f"{name}! {msg}")
        for c in cmds: self.a.rcon.cmd(c.format(dx=random.randint(-6, 6), dz=random.randint(-6, 6)))
        if "보너스" in name:
            for p in self.a.state.players: self.add_coins(p, 20, "랜덤 보너스")

    def backup(self, manual=False):
        self.last_backup = time.time()
        wd = self.f["world_dir"] or self._guess_world()
        if not wd or not Path(wd).exists():
            if manual: self.a.say("백업 실패: config features.world_dir 에 월드 폴더 경로 필요")
            return
        self.a.rcon.cmd("save-all flush"); time.sleep(2)
        out = HERE / "backups"; out.mkdir(exist_ok=True)
        name = out / f"world_{datetime.now():%Y%m%d_%H%M}.zip"
        try:
            with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
                for root, _, files in os.walk(wd):
                    for f in files:
                        if f.endswith(".lock"): continue
                        fp = Path(root) / f; z.write(fp, fp.relative_to(Path(wd).parent))
            olds = sorted(out.glob("world_*.zip"))
            for o in olds[:-self.f["backup_keep"]]: o.unlink()
            mb = name.stat().st_size // 1_000_000
            print(f"💾 백업 완료 {name.name} ({mb}MB)")
            if manual: self.a.say(f"💾 백업 완료 ({mb}MB)")
        except Exception as e:
            print("백업 오류", e)
            if manual: self.a.say(f"백업 오류: {e}")

    def _guess_world(self):
        lp = Path(self.cfg["log_path"])
        for cand in (lp.parent.parent / "world", lp.parent.parent / "saves"):
            if cand.exists(): return str(cand)
        return ""

    def daily_report(self, announce=True):
        pt = {k: self.playtime_min(k) for k in set(self.d["playtime"]) | set(self.d["sessions"])}
        top = sorted(pt.items(), key=lambda x: -x[1])[:3]
        facts = (f"오늘 {date.today()} 접속자 {len([k for k, v in self.d['attend'].items() if v == str(date.today())])}명, "
                 f"플레이타임 상위: {', '.join(f'{k} {v}분' for k, v in top)}, 신고 {len(self.d['reports'])}건, 건의 {len(self.d['suggestions'])}건, "
                 f"총 데스 {sum(self.d['deaths'].values())}, 총 코인 유통 {sum(self.d['coins'].values())}")
        text = facts
        if self.a.brain.key:
            r = self.a.brain.ask("시스템", f"오늘 서버 일지를 아키텍트 말투로 3문장 이내로 써라. 데이터: {facts}. 최근 채팅 분위기도 반영.", is_owner=True)
            if r and "오류" not in r: text = r
        self.d["journal"].append({"day": str(date.today()), "text": text}); self.d["journal"] = self.d["journal"][-60:]; self.save()
        if announce: self.a.say("📓 오늘의 일지: " + text)
        if self.a.discord_out: self.a.discord_out("📓 **서버 일지**\n" + text)
        return text

    # ---------- AI 플레이어 메모 ----------
    def note(self, p, text):
        self.d["notes"].setdefault(p, []).append(text); self.d["notes"][p] = self.d["notes"][p][-10:]; self.save(); return f"{p} 메모: {text}"
    def notes_text(self):
        return "\n".join(f"{p}: {'; '.join(n)}" for p, n in self.d["notes"].items()) or "없음"

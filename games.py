"""
아키텍트 미니게임 엔진 — 바닐라 명령어만으로 돌아가는 25+ 게임
시작: 채팅 !게임 <이름> [옵션]  /  !게임 목록  /  !게임 종료   (또는 AI에게 "좀비 서바이벌 시작해")

구조
  Game: name, desc, duration(초), start(), tick(2초마다), on_chat(), on_death(), end()
  stat 기반 게임은 scoreboard 목표(criteria)를 만들고 끝날 때 점수를 읽어 순위 매김.
"""
import random
import re
import threading
import time

A = None  # Admin 인스턴스 (register 시 주입)

WORDS = ["사과", "바나나", "마인크래프트", "크리퍼", "다이아몬드", "엔더맨", "포켓몬", "피카츄", "레드스톤", "네더라이트",
         "아키텍트", "서버", "좀비", "스켈레톤", "엔더드래곤", "위더", "철골렘", "눈골렘", "주민", "약탈자",
         "꿀벌", "여우", "늑대", "고양이", "앵무새", "돌고래", "거북이", "판다", "라마", "말"]
QUIZ = [("마인크래프트에서 크리퍼가 무서워하는 동물은?", ["고양이", "오셀롯"]), ("엔더드래곤이 사는 차원은?", ["엔드", "디엔드", "the end"]),
        ("다이아몬드 곡괭이로 캘 수 없는 블록은?", ["기반암", "베드락"]), ("철골렘을 만들 때 필요한 철 블록 개수는?", ["4", "네개", "4개"]),
        ("네더로 가는 포탈을 만드는 블록은?", ["흑요석"]), ("물 양동이와 용암 양동이를 섞으면?", ["흑요석", "조약돌"]),
        ("마인크래프트 개발사는?", ["모장", "mojang"]), ("피카츄의 타입은?", ["전기"]), ("한국의 수도는?", ["서울"]),
        ("1 바이트는 몇 비트?", ["8", "8비트"]), ("가장 큰 행성은?", ["목성"]), ("물의 화학식은?", ["h2o"]),
        ("엔더 진주를 던지면?", ["순간이동", "텔레포트"]), ("마을에 나타나는 초록 옷 입은 주민은?", ["니트윗", "바보", "nitwit"]),
        ("좀비가 햇빛을 받으면?", ["불탄다", "불", "타요", "탄다"]), ("3 곱하기 7은?", ["21"]), ("12 더하기 15는?", ["27"]), ("100 나누기 4는?", ["25"])]


# =================================================================== 베이스
class Game:
    name = "게임"; desc = ""; duration = 60; min_players = 1; objective = None; criteria = None; higher_wins = True

    def __init__(self, args):
        self.args = args; self.t0 = time.time(); self.done = False; self.ready = False; self.players = sorted(A.state.players)

    # ---- 헬퍼 ----
    def cmd(self, c): return A.rcon.cmd(c)
    def say(self, t): A.say(t)
    def title(self, t, sub="", target="@a"):
        self.cmd(f'title {target} title {{"text":"{t}","color":"gold","bold":true}}')
        if sub: self.cmd(f'title {target} subtitle {{"text":"{sub}","color":"yellow"}}')
    def sound(self, s="minecraft:entity.player.levelup", target="@a"): self.cmd(f"execute as {target} at @s run playsound {s} master @s ~ ~ ~ 1 1")
    def effect(self, eff, sec, amp=0, target="@a"): self.cmd(f"effect give {target} minecraft:{eff} {sec} {amp} true")
    def setup_board(self):
        if self.objective:
            self.cmd(f"scoreboard objectives remove {self.objective}")
            self.cmd(f"scoreboard objectives add {self.objective} {self.criteria or 'dummy'} \"{self.name}\"")
            self.cmd(f"scoreboard objectives setdisplay sidebar {self.objective}")
            for p in self.players: self.cmd(f"scoreboard players set {p} {self.objective} 0")
    def scores(self):
        out = {}
        for p in self.players:
            m = re.search(r"has (-?\d+)", self.cmd(f"scoreboard players get {p} {self.objective}"))
            out[p] = int(m.group(1)) if m else 0
        return out
    def remaining(self): return max(0, int(self.duration - (time.time() - self.t0)))
    def countdown(self):
        for n in (3, 2, 1):
            self.title(str(n), ""); self.sound("minecraft:block.note_block.pling"); time.sleep(1)
        self.title("시작!", self.name); self.sound()

    # ---- 생애주기 ----
    def start(self): pass
    def tick(self): pass
    def on_chat(self, player, msg): pass
    def on_death(self, player): pass
    def end(self, reason="시간 종료"):
        self.done = True
        if self.objective:
            sc = self.scores(); ranked = sorted(sc.items(), key=lambda x: x[1], reverse=self.higher_wins)
            if ranked:
                w, s = ranked[0]
                self.say(f"🏆 {self.name} 끝 ({reason}). 우승: {w} ({s}점)  " + " · ".join(f"{p} {v}" for p, v in ranked[1:4]))
                self.title("🏆 " + w, f"{self.name} 우승"); self.sound("minecraft:ui.toast.challenge_complete")
                A.features.add_coins(w, 50, f"{self.name} 우승")
                A.features.record_win(w, self.name)
            self.cmd(f"scoreboard objectives remove {self.objective}")
        else:
            self.say(f"{self.name} 끝 ({reason}).")
        self.cleanup()
    def cleanup(self): pass


# =================================================================== 채팅 게임
class NumberGuess(Game):
    name = "숫자 맞추기"; desc = "1~100 사이 숫자. 채팅에 숫자 치면 업/다운"; duration = 180
    def start(self):
        self.n = random.randint(1, 100); self.tries = 0
        self.say(f"1부터 100 사이 숫자를 정했다. 채팅에 숫자를 쳐라. {self.duration}초.")
    def on_chat(self, p, m):
        if m.strip().isdigit():
            g = int(m); self.tries += 1
            if g == self.n: self.say(f"정답! {p}가 {self.tries}번 만에 맞췄다. ({self.n})"); A.features.add_coins(p, 30, "숫자 맞추기"); A.features.record_win(p, self.name); self.done = True; self.cleanup()
            else: A.whisper(p, "⬆ 더 크다" if g < self.n else "⬇ 더 작다")

class WordChain(Game):
    name = "끝말잇기"; desc = "아키텍트와 끝말잇기. 10초 안에 이어라"; duration = 300
    def start(self):
        self.word = random.choice(WORDS); self.used = {self.word}; self.last = time.time(); self.turn = None
        self.say(f"끝말잇기 시작. 내 단어: '{self.word}'. '{self.word[-1]}'(으)로 시작하는 단어를 쳐라.")
    def on_chat(self, p, m):
        w = m.strip()
        if len(w) < 2 or " " in w: return
        if w[0] != self.word[-1]: A.whisper(p, f"'{self.word[-1]}'(으)로 시작해야 한다"); return
        if w in self.used: A.whisper(p, "이미 나온 단어"); return
        # 사전 검증은 AI에게 (짧게)
        ok = A.brain.ask("시스템", f"'{w}'가 실제 한국어 단어(명사)인가? '예' 또는 '아니오'로만.", is_owner=True) if A.brain.key else "예"
        if "아니" in ok: A.whisper(p, f"'{w}'는 단어가 아닌 것 같다"); return
        self.used.add(w); A.features.add_coins(p, 5, "끝말잇기")
        # 아키텍트 차례
        cand = [x for x in WORDS if x[0] == w[-1] and x not in self.used]
        if not cand and A.brain.key:
            r = A.brain.ask("시스템", f"'{w[-1]}'(으)로 시작하는 한국어 명사 하나만 단어만 답해. 이미 쓴 것 제외: {', '.join(self.used)}", is_owner=True)
            r = re.sub(r"[^가-힣]", "", r or "")
            if r and r[0] == w[-1] and r not in self.used: cand = [r]
        if not cand: self.say(f"'{w}'... 내가 졌다. {p} 승리!"); A.features.add_coins(p, 40, "끝말잇기 승"); A.features.record_win(p, self.name); self.done = True; return
        self.word = random.choice(cand); self.used.add(self.word); self.last = time.time()
        self.say(f"{p}: {w} → 나: '{self.word}'. 다음은 '{self.word[-1]}'.")
    def tick(self):
        if time.time() - self.last > 45: self.say("아무도 못 잇는군. 내 승리다."); self.done = True

class Quiz(Game):
    name = "퀴즈"; desc = "5문제 퀴즈. 먼저 맞추면 점수"; duration = 240; objective = "quiz"
    def start(self):
        self.setup_board(); self.qs = random.sample(QUIZ, 5); self.i = -1; self.next_q()
    def next_q(self):
        self.i += 1
        if self.i >= len(self.qs): self.end("문제 끝"); return
        self.q, self.ans = self.qs[self.i]; self.qt = time.time()
        self.say(f"Q{self.i+1}. {self.q}")
    def on_chat(self, p, m):
        if self.done: return
        if any(a in m.lower().replace(" ", "") for a in self.ans):
            self.cmd(f"scoreboard players add {p} {self.objective} 1"); self.say(f"정답! {p} (+1)"); self.sound(); self.next_q()
    def tick(self):
        if time.time() - self.qt > 40 and not self.done: self.say(f"시간 초과. 정답: {self.ans[0]}"); self.next_q()

class AIQuiz(Quiz):
    name = "AI 퀴즈"; desc = "아키텍트가 즉석 출제 (주제 지정 가능: !게임 AI퀴즈 포켓몬)"; objective = "aiquiz"
    def start(self):
        topic = self.args or "마인크래프트, 게임, 상식"
        raw = A.brain.ask("시스템", f"주제 '{topic}'로 짧은 퀴즈 5개. 형식 엄수, 한 줄에 하나: 문제|정답1,정답2  (정답은 짧은 단어, 동의어 콤마)", is_owner=True) if A.brain.key else ""
        qs = []
        for line in (raw or "").splitlines():
            if "|" in line:
                q, a = line.split("|", 1); qs.append((q.strip(" -0123456789.Q"), [x.strip().lower() for x in a.split(",") if x.strip()]))
        self.qs = qs[:5] or random.sample(QUIZ, 5); self.setup_board(); self.i = -1; self.next_q()

class SpeedType(Game):
    name = "스피드 타이핑"; desc = "뜨는 단어를 가장 먼저 치기. 7라운드"; duration = 120; objective = "typing"
    def start(self): self.setup_board(); self.round = 0; self.word = None; self.next_word()
    def next_word(self):
        self.round += 1
        if self.round > 7: self.end("라운드 끝"); return
        self.word = None; time.sleep(random.uniform(1.5, 3.5)); self.word = random.choice(WORDS); self.wt = time.time()
        self.title(self.word, "먼저 쳐라!")
        self.say(f"▶ {self.word}")
    def on_chat(self, p, m):
        if self.word and m.strip() == self.word:
            ms = int((time.time() - self.wt) * 1000); self.cmd(f"scoreboard players add {p} {self.objective} 1"); self.say(f"{p} 승 ({ms}ms)"); self.word = None; self.next_word()
    def tick(self):
        if self.word and time.time() - self.wt > 15: self.say("아무도 못 쳤다."); self.next_word()

class RPS(Game):
    name = "가위바위보"; desc = "아키텍트와 가위바위보 3판"; duration = 90
    def start(self): self.score = {}; self.say("가위/바위/보 중 하나를 쳐라. 3승 먼저 하면 끝.")
    def on_chat(self, p, m):
        m = m.strip()
        if m not in ("가위", "바위", "보"): return
        me = random.choice(["가위", "바위", "보"]); win = {("가위", "보"), ("바위", "가위"), ("보", "바위")}
        if (m, me) in win: self.score[p] = self.score.get(p, 0) + 1; r = f"{p} 승 ({self.score[p]}/3)"
        elif m == me: r = "비김"
        else: r = "내 승"
        self.say(f"{p}: {m} vs 나: {me} → {r}")
        if self.score.get(p, 0) >= 3: self.say(f"🏆 {p}, 가위바위보 챔피언."); A.features.add_coins(p, 30, "가위바위보"); A.features.record_win(p, self.name); self.done = True

class Vote(Game):
    name = "투표"; desc = "!게임 투표 질문? 선택1 선택2 ... (60초)"; duration = 60
    def start(self):
        parts = self.args.split(); q = next((x for x in parts if x.endswith("?")), "투표"); opts = [x for x in parts if not x.endswith("?")] or ["찬성", "반대"]
        self.opts = opts; self.votes = {}; self.say(f"📊 {q}  → " + " / ".join(f"{i+1}.{o}" for i, o in enumerate(opts)) + "  (번호나 이름을 채팅에)")
    def on_chat(self, p, m):
        m = m.strip()
        for i, o in enumerate(self.opts):
            if m == str(i + 1) or m == o: self.votes[p] = o; A.whisper(p, f"투표: {o}")
    def end(self, reason=""):
        self.done = True; tally = {o: sum(1 for v in self.votes.values() if v == o) for o in self.opts}
        self.say("📊 결과: " + " / ".join(f"{o} {n}표" for o, n in sorted(tally.items(), key=lambda x: -x[1])))

class KingGame(Game):
    name = "왕 게임"; desc = "무작위 1명이 왕. 2분간 왕의 명령은 절대"; duration = 120
    def start(self):
        if not self.players: self.done = True; return
        k = random.choice(self.players); self.say(f"👑 왕은... {k}! 2분 동안 왕의 말은 법이다.")
        self.cmd(f"effect give {k} minecraft:glowing {self.duration} 0 true"); self.title("👑 " + k, "왕 게임")

class Assassin(Game):
    name = "암살자"; desc = "한 명이 비밀 암살자. 채팅으로 누가 암살자인지 맞추기 (!지목 이름)"; duration = 180; min_players = 3
    def start(self):
        self.k = random.choice(self.players); A.whisper(self.k, "🗡 너는 암살자다. 들키지 마라.")
        for p in self.players:
            if p != self.k: A.whisper(p, "너는 시민이다. 암살자를 찾아라. (!지목 이름)")
        self.say("암살자 게임 시작. 3분 안에 '!지목 이름' 으로 암살자를 찾아라. 틀리면 그 사람은 탈락.")
        self.out = set()
    def on_chat(self, p, m):
        if p in self.out or not m.startswith("!지목"): return
        t = m.split(maxsplit=1)[1].strip() if " " in m else ""
        if t == self.k: self.say(f"🎯 {p}가 암살자 {self.k}를 찾아냈다! 시민 승리."); A.features.add_coins(p, 40, "암살자 검거"); A.features.record_win(p, self.name); self.done = True
        elif t in self.players: self.out.add(p); self.say(f"{p}의 지목 실패. {p} 탈락.")
    def end(self, reason=""):
        if not self.done: self.say(f"🗡 암살자 {self.k}의 승리. 아무도 못 찾았다."); A.features.add_coins(self.k, 40, "암살자 승"); A.features.record_win(self.k, self.name)
        self.done = True


# =================================================================== 액션 게임 (stat/scoreboard)
class StatGame(Game):
    """scoreboard criteria 로 점수 자동 집계되는 게임 공통"""
    def start(self):
        self.setup_board(); self.pre(); self.say(f"⚔ {self.name}: {self.desc}  ({self.duration}초)"); self.countdown(); self.t0 = time.time()
    def pre(self): pass
    def tick(self):
        r = self.remaining()
        if r in (60, 30, 10, 5, 3, 2, 1): self.cmd(f'title @a actionbar {{"text":"⏱ {r}초","color":"red"}}')

class ZombieSurvival(StatGame):
    name = "좀비 서바이벌"; desc = "몰려오는 좀비를 처치. 처치 수 승부"; duration = 120; objective = "zkills"; criteria = "minecraft.killed:minecraft.zombie"
    def pre(self): self.wave = 0
    def tick(self):
        super().tick()
        if int(time.time() - self.t0) % 15 == 0:
            self.wave += 1; n = 2 + self.wave
            for _ in range(n):
                dx, dz = random.choice([-8, 8]), random.randint(-8, 8)
                self.cmd(f"execute as @a at @s run summon minecraft:zombie ~{dx} ~ ~{dz} {{PersistenceRequired:1b}}")
            self.cmd(f'title @a actionbar {{"text":"🧟 웨이브 {self.wave}","color":"dark_red"}}')
    def cleanup(self): self.cmd("kill @e[type=minecraft:zombie,nbt={PersistenceRequired:1b}]")

class MobHunt(StatGame):
    name = "몹 사냥"; desc = "3분간 적대 몹 처치 수"; duration = 180; objective = "hunt"; criteria = "totalKillCount"

class MinerRace(StatGame):
    name = "광부 레이스"; desc = "5분간 다이아 광석 가장 많이 캐기"; duration = 300; objective = "diamonds"; criteria = "minecraft.mined:minecraft.diamond_ore"
    def pre(self): self.cmd(f"scoreboard objectives add dsd minecraft.mined:minecraft.deepslate_diamond_ore");
    def scores(self):
        s = super().scores()
        for p in self.players:
            m = re.search(r"has (\d+)", self.cmd(f"scoreboard players get {p} dsd")); s[p] += int(m.group(1)) if m else 0
        return s
    def cleanup(self): self.cmd("scoreboard objectives remove dsd")

class Fishing(StatGame):
    name = "낚시 대회"; desc = "3분간 물고기 많이 잡기"; duration = 180; objective = "fish"; criteria = "minecraft.custom:minecraft.fish_caught"
    def pre(self): self.cmd("give @a minecraft:fishing_rod"); self.cmd("weather rain")

class Marathon(StatGame):
    name = "마라톤"; desc = "90초간 가장 멀리 달리기"; duration = 90; objective = "run"; criteria = "minecraft.custom:minecraft.walk_one_cm"
    def pre(self): self.effect("speed", self.duration, 1)
    def scores(self): return {p: v // 100 for p, v in super().scores().items()}   # m 단위

class JumpCount(StatGame):
    name = "점프 대결"; desc = "30초간 점프 횟수"; duration = 30; objective = "jumps"; criteria = "minecraft.custom:minecraft.jump"

class Snowball(StatGame):
    name = "눈싸움"; desc = "60초간 눈덩이 가장 많이 던지기"; duration = 60; objective = "snow"; criteria = "minecraft.used:minecraft.snowball"
    def pre(self): self.cmd("give @a minecraft:snowball 64"); self.cmd("give @a minecraft:snowball 64")
    def cleanup(self): self.cmd("clear @a minecraft:snowball")

class TreasureHunt(StatGame):
    name = "보물찾기"; desc = "근처 어딘가에 숨긴 상자를 먼저 열기"; duration = 240; objective = "chest"; criteria = "minecraft.custom:minecraft.open_chest"
    def pre(self):
        p = random.choice(self.players) if self.players else "@r"
        dx, dz = random.randint(-40, 40), random.randint(-40, 40)
        self.cmd(f"execute at {p} run setblock ~{dx} ~ ~{dz} minecraft:chest{{Items:[{{Slot:0b,id:\"minecraft:diamond\",Count:5b}},{{Slot:1b,id:\"minecraft:golden_apple\",Count:3b}}]}}")
        self.cmd(f"execute at {p} positioned ~{dx} ~ ~{dz} run summon minecraft:firework_rocket ~ ~ ~")
        self.say(f"{p} 근처 {abs(dx)+abs(dz)}블록 안 어딘가에 상자가 있다. 먼저 여는 자가 가져간다.")

class Duel(Game):
    name = "결투"; desc = "!게임 결투 A B — 1대1. 둘을 마주보게 하고 킷 지급"; duration = 180
    def start(self):
        a = self.args.split()
        if len(a) < 2: self.say("사용법: !게임 결투 이름1 이름2"); self.done = True; return
        self.a, self.b = a[0], a[1]
        for p in (self.a, self.b):
            self.cmd(f"clear {p}"); self.cmd(f"give {p} minecraft:iron_sword"); self.cmd(f"give {p} minecraft:bow"); self.cmd(f"give {p} minecraft:arrow 16"); self.cmd(f"give {p} minecraft:golden_apple 2")
            self.cmd(f"effect give {p} minecraft:instant_health 1 10 true"); self.cmd(f"effect give {p} minecraft:saturation 1 10 true")
        self.cmd(f"execute at {self.a} run tp {self.b} ~10 ~ ~ facing entity {self.a}")
        self.say(f"⚔ 결투: {self.a} vs {self.b}. 3분."); self.countdown()
    def on_death(self, p):
        if p in (self.a, self.b):
            w = self.b if p == self.a else self.a; self.say(f"⚔ {w} 승리!"); self.title("⚔ " + w, "결투 승"); A.features.add_coins(w, 50, "결투"); A.features.record_win(w, self.name); self.done = True

class TagGame(Game):
    name = "술래잡기"; desc = "술래(빛남)가 닿으면 술래 교체. 끝날 때 술래가 패배"; duration = 120; min_players = 2
    def start(self):
        it = random.choice(self.players); self.it = it
        self.cmd("tag @a remove it"); self.cmd(f"tag {it} add it"); self.cmd(f"effect give {it} minecraft:glowing 1000 0 true"); self.cmd(f"effect give {it} minecraft:speed 1000 0 true")
        self.say(f"🏃 술래잡기! 술래: {it}. 2분 뒤 술래인 사람이 진다."); self.countdown()
    def tick(self):
        out = self.cmd("execute as @a[tag=!it] at @s if entity @p[tag=it,distance=..1.6] run tag @s add it_new")
        if "passed" in out.lower() or "it_new" in self.cmd("execute if entity @a[tag=it_new]").lower():
            new = self.cmd("execute if entity @a[tag=it_new]")
            self.cmd("effect clear @a[tag=it] minecraft:glowing"); self.cmd("effect clear @a[tag=it] minecraft:speed"); self.cmd("tag @a[tag=it] remove it")
            self.cmd("tag @a[tag=it_new] add it"); self.cmd("tag @a remove it_new")
            self.cmd("effect give @a[tag=it] minecraft:glowing 1000 0 true"); self.cmd("effect give @a[tag=it] minecraft:speed 1000 0 true")
            self.say("👉 술래 바뀜!")
    def end(self, reason=""):
        self.done = True
        loser = re.sub(r".*?(\S+)$", r"\1", "")  # 이름 조회가 불확실하니 glow 끄고 안내만
        self.say("🏃 술래잡기 끝. 지금 빛나는 사람이 패배!"); self.cleanup()
    def cleanup(self): self.cmd("effect clear @a minecraft:glowing"); self.cmd("effect clear @a minecraft:speed"); self.cmd("tag @a remove it"); self.cmd("tag @a remove it_new")

class HideSeek(Game):
    name = "숨바꼭질"; desc = "술래는 30초 눈 가림. 이후 3분간 찾기(2블록 접근)"; duration = 210; min_players = 2
    def start(self):
        self.seeker = random.choice(self.players); self.found = set()
        self.cmd(f"effect give {self.seeker} minecraft:blindness 30 0 true"); self.cmd(f"effect give {self.seeker} minecraft:slowness 30 5 true")
        self.say(f"🙈 술래: {self.seeker}. 30초 안에 숨어라!")
    def tick(self):
        if time.time() - self.t0 < 30: return
        if not hasattr(self, "go"): self.go = True; self.say("👀 술래 출발!"); self.cmd(f"effect give {self.seeker} minecraft:speed 180 0 true")
        for p in self.players:
            if p == self.seeker or p in self.found: continue
            if "passed" in self.cmd(f"execute as {p} at @s if entity @a[name={self.seeker},distance=..2] run say found").lower() or "found" in self.cmd(f"execute as {p} at @s if entity @a[name={self.seeker},distance=..2]").lower():
                self.found.add(p); self.say(f"🔍 {p} 발견! ({len(self.found)}/{len(self.players)-1})")
        if len(self.found) >= len(self.players) - 1: self.say(f"👀 술래 {self.seeker} 전원 발견! 술래 승."); A.features.record_win(self.seeker, self.name); self.done = True
    def end(self, reason=""):
        if not self.done: hid = [p for p in self.players if p != self.seeker and p not in self.found]; self.say(f"🙈 끝! 안 들킨 사람: {', '.join(hid)}"); [A.features.record_win(p, self.name) for p in hid]
        self.done = True

class DodgeFire(Game):
    name = "화염구 피하기"; desc = "하늘에서 화염구. 60초 생존"; duration = 60
    def start(self): self.alive = set(self.players); self.say("☄ 하늘을 봐라. 60초 버텨라."); self.countdown()
    def tick(self):
        for _ in range(3): self.cmd(f"execute as @a at @s run summon minecraft:fireball ~{random.randint(-4,4)} ~20 ~{random.randint(-4,4)} {{power:[0.0,-1.0,0.0],ExplosionPower:1}}")
    def on_death(self, p):
        if p in self.alive: self.alive.discard(p); self.say(f"☄ {p} 격추. 남은 생존자 {len(self.alive)}")
    def end(self, reason=""):
        self.done = True; s = sorted(self.alive); self.say(f"☄ 생존자: {', '.join(s) or '없음'}"); [A.features.record_win(p, self.name) for p in s]; self.cmd("kill @e[type=minecraft:fireball]")

class LightningDodge(Game):
    name = "번개 피하기"; desc = "주변에 번개 낙뢰. 45초 생존"; duration = 45
    def start(self): self.alive = set(self.players); self.cmd("weather thunder"); self.say("⚡ 번개가 친다. 계속 움직여라."); self.countdown()
    def tick(self):
        for _ in range(2): self.cmd(f"execute as @a at @s run summon minecraft:lightning_bolt ~{random.randint(-3,3)} ~ ~{random.randint(-3,3)}")
    def on_death(self, p):
        if p in self.alive: self.alive.discard(p); self.say(f"⚡ {p} 감전.")
    def end(self, reason=""):
        self.done = True; self.cmd("weather clear"); s = sorted(self.alive); self.say(f"⚡ 생존자: {', '.join(s) or '없음'}"); [A.features.record_win(p, self.name) for p in s]

class RussianRoulette(Game):
    name = "러시안 룰렛"; desc = "5초마다 무작위 1명에게 번개. 마지막 생존자 승"; duration = 120; min_players = 2
    def start(self): self.alive = list(self.players); self.say("🔫 러시안 룰렛. 운을 시험하라."); self.countdown()
    def tick(self):
        if int(time.time() - self.t0) % 5 == 0 and len(self.alive) > 1:
            v = random.choice(self.alive); self.cmd(f"execute at {v} run summon minecraft:lightning_bolt ~ ~ ~"); self.cmd(f'title {v} title {{"text":"💥","color":"red"}}')
    def on_death(self, p):
        if p in self.alive: self.alive.remove(p); self.say(f"🔫 {p} 탈락. 남은 {len(self.alive)}명")
        if len(self.alive) == 1: w = self.alive[0]; self.say(f"🏆 {w} 생존!"); A.features.add_coins(w, 50, "룰렛"); A.features.record_win(w, self.name); self.done = True

class Boss(Game):
    name = "보스전"; desc = "강화 보스 소환 (!게임 보스전 위더 / 가디언 / 좀비왕). 처치하면 보상"; duration = 600
    def start(self):
        kind = (self.args or "좀비왕").strip(); p = random.choice(self.players) if self.players else "@r"
        if "위더" in kind: self.cmd(f"execute at {p} run summon minecraft:wither ~ ~5 ~10 {{CustomName:'\"아키텍트의 사도\"'}}"); self.target = "minecraft:wither"
        elif "가디언" in kind: self.cmd(f"execute at {p} run summon minecraft:elder_guardian ~ ~ ~8 {{CustomName:'\"심해의 감시자\"'}}"); self.target = "minecraft:elder_guardian"
        else:
            self.cmd(f"execute at {p} run summon minecraft:zombie ~ ~ ~8 {{CustomName:'\"좀비왕\"',CustomNameVisible:1b,Health:200f,Attributes:[{{Name:generic.max_health,Base:200}},{{Name:generic.attack_damage,Base:10}},{{Name:generic.movement_speed,Base:0.33}}],ArmorItems:[{{}},{{}},{{}},{{id:\"minecraft:netherite_helmet\",Count:1b}}],HandItems:[{{id:\"minecraft:diamond_axe\",Count:1b}},{{}}],PersistenceRequired:1b,Glowing:1b}}")
            self.target = "minecraft:zombie"
        self.cmd('bossbar add architect_boss {"text":"보스전"}'); self.cmd("bossbar set architect_boss players @a"); self.cmd("bossbar set architect_boss color red")
        self.say(f"👹 보스 '{kind}' 강림. 모두 힘을 합쳐라. 보상: 네더라이트 조각."); self.title("👹 보스전", kind)
    def tick(self):
        alive = self.cmd(f"execute if entity @e[type={self.target},nbt={{PersistenceRequired:1b}}]") if self.target == "minecraft:zombie" else self.cmd(f"execute if entity @e[type={self.target}]")
        if "passed" not in alive.lower() and "found" not in alive.lower() and time.time() - self.t0 > 10:
            self.say("👹 보스 처치! 전원 보상."); self.cmd("give @a minecraft:netherite_scrap 2"); self.cmd("give @a minecraft:golden_apple 1"); [A.features.add_coins(p, 30, "보스 처치") for p in self.players]; self.done = True
    def cleanup(self): self.cmd("bossbar remove architect_boss")

class LowGravity(Game):
    name = "무중력 파티"; desc = "90초간 슈퍼점프 + 천천히 낙하"; duration = 90
    def start(self): self.effect("jump_boost", self.duration, 6); self.effect("slow_falling", self.duration, 0); self.say("🌙 무중력 모드. 90초간 날아다녀라."); self.title("🌙 무중력", "90초")
    def cleanup(self): self.cmd("effect clear @a minecraft:jump_boost"); self.cmd("effect clear @a minecraft:slow_falling")

class Parkour(Game):
    name = "파쿠르"; desc = "!게임 파쿠르 x y z — 그 좌표 2블록 안에 먼저 도착"; duration = 300
    def start(self):
        a = self.args.split()
        if len(a) < 3: self.say("사용법: !게임 파쿠르 x y z"); self.done = True; return
        self.x, self.y, self.z = a[:3]; self.arrived = []
        self.cmd(f"execute positioned {self.x} {self.y} {self.z} run particle minecraft:end_rod ~ ~1 ~ 1 1 1 0 200"); self.say(f"🏁 목표: ({self.x}, {self.y}, {self.z}). 먼저 도착하라."); self.countdown()
    def tick(self):
        self.cmd(f"execute positioned {self.x} {self.y} {self.z} run particle minecraft:end_rod ~ ~1 ~ 0.5 0.5 0.5 0 30")
        for p in self.players:
            if p in self.arrived: continue
            r = self.cmd(f"execute as {p} at @s if entity @s[x={self.x},y={self.y},z={self.z},distance=..2]")
            if "passed" in r.lower() or "test passed" in r.lower():
                self.arrived.append(p); t = int(time.time() - self.t0); self.say(f"🏁 {p} 도착! {t}초 ({len(self.arrived)}등)")
                if len(self.arrived) == 1: A.features.add_coins(p, 40, "파쿠르 1등"); A.features.record_win(p, self.name)
        if len(self.arrived) >= len(self.players): self.done = True

class BuildBattle(Game):
    name = "건축 배틀"; desc = "주제 주고 5분 건축 → 아키텍트가 채팅 투표로 판정"; duration = 300
    def start(self):
        self.topic = self.args or random.choice(["집", "탑", "동물", "로봇", "포켓몬", "우주선", "나무", "성", "자동차", "괴물"])
        self.cmd("gamemode creative @a"); self.say(f"🏗 건축 배틀! 주제: '{self.topic}'. 5분. 끝나면 투표."); self.title("🏗 " + self.topic, "5분")
    def end(self, reason=""):
        self.done = True; self.cmd("gamemode survival @a"); self.say("🏗 시간 끝! 손을 떼라. 이제 '!게임 투표 최고건축은? 이름1 이름2 ...' 로 투표해라.")

class HotPotato(Game):
    name = "폭탄 돌리기"; desc = "폭탄(빛남) 든 사람이 닿아서 넘기기. 15초마다 폭발"; duration = 120; min_players = 2
    def start(self): self.bomb = random.choice(self.players); self.alive = list(self.players); self.fuse = time.time(); self._give(); self.say(f"💣 폭탄 돌리기! {self.bomb}부터. 닿아서 넘겨라."); self.countdown()
    def _give(self): self.cmd("tag @a remove bomb"); self.cmd(f"tag {self.bomb} add bomb"); self.cmd("effect clear @a minecraft:glowing"); self.cmd(f"effect give {self.bomb} minecraft:glowing 1000 0 true")
    def tick(self):
        r = self.cmd("execute as @a[tag=!bomb] at @s if entity @p[tag=bomb,distance=..1.6]")
        if "passed" in r.lower():
            cands = [p for p in self.alive if p != self.bomb]
            if cands:
                # 가장 가까운 사람을 모르니 랜덤 인접자 → 간단화
                self.bomb = random.choice(cands); self._give(); self.say(f"💣 → {self.bomb}")
        if time.time() - self.fuse > 15:
            self.cmd(f"execute at {self.bomb} run summon minecraft:tnt ~ ~ ~ {{Fuse:0}}"); self.say(f"💥 {self.bomb} 폭발!")
            if self.bomb in self.alive: self.alive.remove(self.bomb)
            if len(self.alive) <= 1: w = self.alive[0] if self.alive else None; self.say(f"🏆 {w} 생존!" if w else "전멸."); w and A.features.record_win(w, self.name); self.done = True; return
            self.bomb = random.choice(self.alive); self.fuse = time.time(); self._give()
    def cleanup(self): self.cmd("tag @a remove bomb"); self.cmd("effect clear @a minecraft:glowing")

class Spleef(Game):
    name = "스플리프"; desc = "!게임 스플리프 x y z — 그 위치에 눈 경기장 생성, 삽으로 바닥 파기. 떨어지면 탈락"; duration = 180; min_players = 2
    def start(self):
        a = self.args.split()
        if len(a) < 3: self.say("사용법: !게임 스플리프 x y z (경기장 중심, 공중 추천)"); self.done = True; return
        x, y, z = map(int, a[:3]); self.y = y; self.alive = list(self.players)
        self.cmd(f"fill {x-10} {y} {z-10} {x+10} {y} {z+10} minecraft:snow_block"); self.cmd(f"fill {x-11} {y-6} {z-11} {x+11} {y-6} {z+11} minecraft:magma_block")
        self.cmd(f"tp @a {x} {y+1} {z}"); self.cmd("give @a minecraft:diamond_shovel"); self.cmd("gamemode survival @a")
        self.say("❄ 스플리프! 상대 발밑을 파라. 떨어지면 탈락."); self.countdown()
    def tick(self):
        for p in list(self.alive):
            if "passed" in self.cmd(f"execute as {p} at @s if entity @s[y=-64,dy={self.y-3+64}]").lower(): self.alive.remove(p); self.cmd(f"gamemode spectator {p}"); self.say(f"❄ {p} 낙하!")
        if len(self.alive) <= 1: w = self.alive[0] if self.alive else None; self.say(f"🏆 {w} 승리!" if w else "전원 낙하."); w and A.features.add_coins(w, 50, "스플리프") and A.features.record_win(w, self.name); self.done = True
    def cleanup(self): self.cmd("gamemode survival @a"); self.cmd("clear @a minecraft:diamond_shovel")

class OneInTheChamber(Game):
    name = "한 발 승부"; desc = "화살 1개 + 검. 맞으면 즉사(약화 효과). 3분 킬 수"; duration = 180; objective = "oitc"; criteria = "playerKillCount"; min_players = 2
    def start(self):
        self.setup_board(); self.cmd("clear @a"); self.cmd("give @a minecraft:bow"); self.cmd("give @a minecraft:arrow 1"); self.cmd("give @a minecraft:wooden_sword")
        self.cmd("gamemode survival @a"); self.say("🏹 화살 한 발. 킬하면 화살 보충. 3분."); self.countdown()
    def on_death(self, p): self.cmd("give @a[scores={oitc=1..}] minecraft:arrow 1")
    def cleanup(self): self.cmd("clear @a minecraft:bow"); self.cmd("clear @a minecraft:arrow"); self.cmd("clear @a minecraft:wooden_sword")

class SumoGame(Game):
    name = "스모"; desc = "!게임 스모 A B x y z — 작은 단상에서 밀어내기"; duration = 120
    def start(self):
        a = self.args.split()
        if len(a) < 5: self.say("사용법: !게임 스모 이름1 이름2 x y z"); self.done = True; return
        self.a, self.b = a[0], a[1]; x, y, z = map(int, a[2:5]); self.y = y
        self.cmd(f"fill {x-3} {y} {z-3} {x+3} {y} {z+3} minecraft:quartz_block"); self.cmd(f"tp {self.a} {x-2} {y+1} {z}"); self.cmd(f"tp {self.b} {x+2} {y+1} {z}")
        self.cmd(f"clear {self.a}"); self.cmd(f"clear {self.b}"); self.cmd(f"effect give {self.a} minecraft:resistance 200 10 true"); self.cmd(f"effect give {self.b} minecraft:resistance 200 10 true")
        self.say(f"🤼 스모: {self.a} vs {self.b}. 맨손으로 밀어내라."); self.countdown()
    def tick(self):
        for p, o in ((self.a, self.b), (self.b, self.a)):
            if "passed" in self.cmd(f"execute as {p} at @s if entity @s[y=-64,dy={self.y-2+64}]").lower(): self.say(f"🤼 {o} 승리!"); A.features.add_coins(o, 30, "스모"); A.features.record_win(o, self.name); self.done = True; return
    def cleanup(self): self.cmd(f"effect clear {self.a} minecraft:resistance"); self.cmd(f"effect clear {self.b} minecraft:resistance")

class CreeperRun(Game):
    name = "크리퍼 런"; desc = "크리퍼 떼가 쫓아옴. 60초 생존"; duration = 60
    def start(self): self.alive = set(self.players); self.say("💚 크리퍼가 온다. 달려라!"); self.countdown()
    def tick(self):
        if int(time.time() - self.t0) % 8 == 0: self.cmd("execute as @a at @s run summon minecraft:creeper ~5 ~ ~5 {PersistenceRequired:1b}"); self.cmd("execute as @a at @s run summon minecraft:creeper ~-5 ~ ~-5 {PersistenceRequired:1b}")
    def on_death(self, p):
        if p in self.alive: self.alive.discard(p); self.say(f"💥 {p} 폭사.")
    def end(self, reason=""): self.done = True; s = sorted(self.alive); self.say(f"💚 생존자: {', '.join(s) or '없음'}"); [A.features.record_win(p, self.name) for p in s]; self.cleanup()
    def cleanup(self): self.cmd("kill @e[type=minecraft:creeper,nbt={PersistenceRequired:1b}]")


GAMES = {g.name: g for g in [NumberGuess, WordChain, Quiz, AIQuiz, SpeedType, RPS, Vote, KingGame, Assassin,
                              ZombieSurvival, MobHunt, MinerRace, Fishing, Marathon, JumpCount, Snowball, TreasureHunt,
                              Duel, TagGame, HideSeek, DodgeFire, LightningDodge, RussianRoulette, Boss, LowGravity,
                              Parkour, BuildBattle, HotPotato, Spleef, OneInTheChamber, SumoGame, CreeperRun]}
ALIASES = {"ai퀴즈": "AI 퀴즈", "ai 퀴즈": "AI 퀴즈", "좀비": "좀비 서바이벌", "타이핑": "스피드 타이핑", "숫자": "숫자 맞추기", "술래": "술래잡기",
           "룰렛": "러시안 룰렛", "보스": "보스전", "무중력": "무중력 파티", "폭탄": "폭탄 돌리기", "건축": "건축 배틀", "크리퍼": "크리퍼 런", "낚시": "낚시 대회", "한발": "한 발 승부"}


class GameManager:
    def __init__(self, admin):
        global A; A = admin; self.admin = admin; self.current = None

    def list_text(self):
        return "🎮 미니게임 " + str(len(GAMES)) + "개: " + ", ".join(GAMES.keys()) + "\n시작: !게임 <이름> [옵션]  종료: !게임 종료"

    def find(self, name):
        n = name.strip().lower().replace(" ", "")
        for k in GAMES:
            if k.lower().replace(" ", "") == n: return GAMES[k]
        if n in ALIASES: return GAMES[ALIASES[n]]
        for k in GAMES:
            if n and n in k.lower().replace(" ", ""): return GAMES[k]
        return None

    def start(self, name, args=""):
        if self.current and not self.current.done: return f"이미 '{self.current.name}' 진행 중. !게임 종료 먼저."
        cls = self.find(name)
        if not cls: return f"'{name}' 게임 없음.\n" + self.list_text()
        if len(self.admin.state.players) < cls.min_players: return f"{cls.name}은(는) 최소 {cls.min_players}명 필요 (현재 {len(self.admin.state.players)})"
        g = cls(args); self.current = g
        def run():
            try: g.start()
            except Exception as e: self.admin.say(f"시작 실패: {e}"); g.done = True
            g.ready = True
        threading.Thread(target=run, daemon=True).start()
        return f"{g.name} 준비 중..."

    def stop(self):
        if self.current and not self.current.done: self.current.end("강제 종료"); return "게임 종료"
        return "진행 중인 게임 없음"

    def tick(self):
        g = self.current
        if not g or g.done or not g.ready: return
        try:
            g.tick()
            if not g.done and g.remaining() <= 0: g.end()
        except Exception as e: self.admin.say(f"게임 오류: {e}"); g.done = True

    def on_chat(self, p, m):
        g = self.current
        if g and not g.done:
            try: g.on_chat(p, m)
            except Exception as e: print("game chat err", e)

    def on_death(self, p):
        g = self.current
        if g and not g.done:
            try: g.on_death(p)
            except Exception as e: print("game death err", e)

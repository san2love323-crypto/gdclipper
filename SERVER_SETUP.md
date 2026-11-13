# 내 PC에 Fabric 서버 + 음성채팅 + 아키텍트 세팅 (윈도우)

## 1. 자바 21
https://adoptium.net → **Temurin 21 (JDK) Windows x64 .msi** → 설치할 때 "Set JAVA_HOME variable" 체크
cmd 새로 열고 `java -version` → 21 나오면 OK

## 2. Fabric 서버 (1.21.1)
1. 폴더 만들기: `C:\mcserver`
2. https://fabricmc.net/use/server/ → Minecraft 1.21.1 / Loader 최신 → **Download Server Jar** → `C:\mcserver\fabric-server.jar` 로 저장
3. `C:\mcserver\start.bat` 만들기 (메모장):
   ```
   @echo off
   java -Xms2G -Xmx4G -jar fabric-server.jar nogui
   pause
   ```
   (램 8GB PC면 `-Xmx3G`, 16GB면 `-Xmx6G`)
4. `start.bat` 실행 → 멈추면 `eula.txt` 열어서 `eula=true` → 다시 실행 → 월드 생성되면 `stop`

## 3. 모드 넣기 (`C:\mcserver\mods\`)
| 모드 | 어디서 | 필수 |
|---|---|---|
| Fabric API | https://modrinth.com/mod/fabric-api (1.21.1용) | ✅ |
| Simple Voice Chat | https://modrinth.com/plugin/simple-voice-chat (Fabric 1.21.1) | ✅ |
| **architect-voice** | 네 GitHub `gong-mc-voice` 저장소 Releases (아래 5번) | ✅ |

플레이어도 **Fabric API + Simple Voice Chat**을 클라이언트 mods 에 넣어야 음성 됨 (모드 없는 사람도 접속은 되고 채팅으로만).

## 4. server.properties
```
enable-rcon=true
rcon.port=25575
rcon.password=비번
online-mode=true
motd=아키텍트가 지켜보는 서버
```
Simple Voice Chat은 UDP **24454** 포트 씀 → 공유기 포트포워딩에 **25565 TCP + 24454 UDP** 둘 다.
(`config/voicechat/voicechat-server.properties` 에서 포트 바꿀 수 있음)

## 5. architect-voice 모드 빌드 (GitHub Actions, PC 불필요)
1. GitHub 새 저장소 `gong-mc-voice` → `voice-mod` 폴더 **안의 내용**을 루트에 업로드 (gradlew, build.gradle, src, .github ...)
2. Settings → Actions → General → Workflow permissions → **Read and write**
3. Actions 탭에서 빌드 끝나면 Releases → `architect-voice-1.0.0.jar` 다운 → `C:\mcserver\mods\`

## 6. 아키텍트 실행
```
cd 아키텍트폴더
pip install requests numpy edge-tts miniaudio pyttsx3
python mc_admin.py
```
config.json: `log_path` = `C:/mcserver/logs/latest.log`, rcon 비번, groq 키, owner_names.
서버 켜져 있으면 콘솔에 `모드 연결됨` → 게임 채팅에 "음성 회선 연결" 뜸.

## 7. 말로 해보기 (음성채팅 키 기본 `V` 누른 채)
- "아키텍트, 지금 몇 시야" → 하늘에서 대답
- (주인) "아키텍트, 밤으로 바꿔" / "좀비 서바이벌 시작해"
- 퀴즈 중엔 정답을 그냥 말하면 됨
- 소리 지르면 ⚡ 번개 (끄기: config voice.shout_event=false)
- 작게 속삭이면 아키텍트도 너한테만 속삭임
- `!암호문 설정 열려라참깨 100 64 -20` → 그 블록 앞에서 "열려라 참깨" 하면 문 열림
- `!따라해` → 아키텍트가 네 말을 따라함 (친구 놀리기용)
- `!음성 꺼` / `!음성 켜`

## 문제 생기면
- 모드 연결 안 됨 → 아키텍트가 서버보다 먼저 켜져 있어야 함 (모드가 3초마다 재접속 시도하니 순서 바뀌어도 결국 붙음)
- 목소리 안 들림 → 플레이어 클라이언트에 Simple Voice Chat 있는지, 24454 UDP 열렸는지, 게임 안 음성 설정에서 볼륨
- 인식 이상 → `mc_admin` 콘솔에 `🎤` 줄 보고 Whisper가 뭘 들었는지 확인

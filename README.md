# Icon Painter — 지메 안에서 직접 그리는 커스텀 아이콘 (Geode 모드, 2.2091)

## 빌드 (명령어 직접 입력 — bat 파일 없음, 스마트 앱 컨트롤에 안 걸림)
"x64 Native Tools Command Prompt" 에서:
```
cd iconpainter 폴더 경로
cmake -B build -A x64
cmake --build build --config Release
```
→ `build\Release\matjang.iconpainter.geode` 를 `지메폴더\geode\mods\` 에 복사 → 지메 재시작
(GEODE_SDK 가 없다고 나오면 먼저 `geode sdk install` → 새 창)

## 쓰는 법
1. 아이콘 창(차고) 오른쪽의 **DRAW** 버튼
2. 레이어 고르고 그리기
   - **L1 COL1** : 내 1번 색으로 칠해지는 구역
   - **L2 COL2** : 내 2번 색으로 칠해지는 구역 (L1 **뒤**에 깔림)
   - **L3 FREE** : 고정색 (맨 **앞**). 검정 테두리·눈·입·불러온 사진
   - L1/L2 는 색 대신 밝기 4단계만 고름 (색은 차고에서 고른 색이 입혀짐)
3. PEN / ERASER / FILL / SIZE(1~3) / UNDO / CLEAR(지금 레이어만)
4. **IMG (JPG/PNG)** : 사진 불러오기 → 가운데를 정사각형으로 잘라 32x32 로 줄여서 L3 에 들어감
5. **SAVE** → 지금 고른 큐브가 내 그림으로 바뀜. **USE: OFF** 로 저장하면 원래 아이콘
6. 왼쪽 위 화살표 / ESC = 저장 안 하고 나가기

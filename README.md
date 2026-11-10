# GD Clipper (Geode 모드, 단독 실행)

앱 없이 이 모드만으로 동작. 맵에 들어가면 녹화 시작 → 클리어하면 `_cut.mp4`(클리어한 시도) + `_full.mp4`(입장~클리어 전체) 저장 → 못 깨고 나가면 삭제.
첫 실행 때 ffmpeg(약 90MB)를 자동으로 받음. 설정은 지오드 메뉴 → GD Clipper → 톱니.
영상만 녹화됨(소리 없음). AI 컷편집은 아직 앱(gdclipper.py) 쪽에만 있음.

## .geode 파일 만들기 (GitHub가 빌드)
1. github.com 에서 새 레포 생성 (예: gd-clipper-mod)
2. 이 폴더 내용물을 전부 업로드 (`.github/workflows/build.yml` 포함 - 숨김 폴더 주의)
3. 레포 Actions 탭 → "Build Geode Mod" 실행이 초록색이 되면 → 맨 아래 Artifacts → `GDClipper-geode` 다운
4. zip 안의 `yujun566.gdclipper.geode` 가 모드 파일

## 설치
지메 → 지오드 메뉴 → 왼쪽 "파일에서 설치"(종이+ 아이콘) → `.geode` 선택 → 재시작
(또는 `Geometry Dash\geode\mods\` 폴더에 직접 복사)

## 버전 맞추기
`mod.json` 의 `"geode"`, `"gd"` 와 `build.yml` 의 `sdk:` 세 군데를 설치된 버전에 맞춤.
| 지메 | 지오드 | mod.json geode / gd.win | build.yml sdk |
|---|---|---|---|
| 2.2081 | v5.11.2 | 5.11.2 / 2.2081 | v5.11.2 |
| 2.2074 | v4.10.0 | 4.10.0 / 2.2074 | v4.10.0 |

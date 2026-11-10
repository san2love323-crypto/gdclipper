# GD Clipper (Geode 모드)

지메 안에서 "맵 입장 / 시도 / 죽음 / 클리어 / 나감" 이벤트를 GD Clipper 앱으로 보내는 모드.
녹화·AI 편집은 앱(gdclipper.py / GD Clipper.exe)이 함. DLL 주입이 필요 없어짐.

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

## 앱 쪽
`GDClipperHook.dll` 을 clipper 폴더에서 지우면 앱이 자동으로 "Geode 모드 방식"으로 동작 (주입 안 함).
앱 로그에 `훅 연결됨` 이 뜨면 모드와 연결된 것.

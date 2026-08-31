# image-crawler-app — 인수인계 문서

## 뭐하는 프로젝트
웹페이지 URL을 주면 그 안의 이미지를 찾아서 다운로드하고(포맷 변환 포함),
선택적으로 구글 드라이브에 업로드까지 해주는 Flask 앱.

## 기능 → 코드
| 기능 | 코드 위치 |
|---|---|
| 웹페이지 HTML에서 이미지 URL 추출(srcset 중 가장 큰 것 선택 등) | `app.py` |
| HTTP 요청(재시도, 브라우저 User-Agent 위장) | `net.py` |
| 다운로드한 이미지 포맷 변환(AVIF 지원 포함), 원본 보존 여부 | `pipeline.py` (`IMAGE_KEEP_ORIGINAL` 환경변수) |
| 다운로드 기록 저장 | `db.py` → `registry.db` (git 제외) |
| 구글 드라이브 업로드 | `drive.py` — OAuth (`drive.file`, `spreadsheets` 스코프) |
| 브라우저 못 여는 AI(claude.ai, Gemini 등)용 등록 큐 | `sheets.py` + `queue_config.py` (스프레드시트 ID) |
| 큐 감시 → 이미지 URL 추출 | `extractor_worker.py` (10초 폴링) |
| 큐 감시 → 실제 다운로드 | `download_worker.py` (10초 폴링) |
| 실행 (서버 + 워커 2개 전부) | `run.bat` |

## 큐 아키텍처 (왜 있는가)
로컬 앱은 `127.0.0.1`에서만 열려서, 브라우저 안에서만 도는 AI(claude.ai 웹, Gemini 등)는
직접 호출할 방법이 없음 (Claude Code처럼 로컬 셸 접근이 있는 경우는 이 문제 자체가 없고,
그냥 `app.py`의 `/api/images/download-batch` 등을 바로 curl하면 됨 — 큐 필요 없음).

그래서 그런 AI들도 할 수 있는 유일한 것(구글 시트에 행 쓰기)을 다리로 씀:
```
AI → 구글 시트 submissions 탭에 행 추가 (status=pending)
   → extractor_worker가 감지 → 페이지면 이미지 URL들 추출, 이미 이미지 URL이면 그대로 → images 탭에 기록
   → download_worker가 감지 → 실제 다운로드 → registry.db + 시트 상태 업데이트 (downloaded/failed)
```
시트 열 구조는 `sheets.py`의 `SUBMISSIONS_HEADERS`/`IMAGES_HEADERS` 참고.
스프레드시트 ID는 `queue_config.py`에 하드코딩돼있음 (새 환경이면 `sheets.create_spreadsheet()`로 새로 만들어서 갱신).

**실제 종단간 테스트 완료됨**: 시트에 수동으로 행 추가 → 워커 건드리지 않고 5초 안에 자동 다운로드까지 확인함.

## 필요한 것 / 절대 커밋하면 안 되는 것
- `client_secret.json` — 구글 OAuth 클라이언트 시크릿. **이미 `.gitignore`로 제외돼 있음**, 새 환경에서 직접 발급해서 채워야 함
- `token.json` — 최초 인증 후 로컬에 자동 생성 (git 제외)
- `requirements.txt`로 의존성 설치

## 알아두면 좋은 점
- git 커밋 로그를 보면 한때 MCP 서버/ChatGPT Actions 연동, Render/Railway 배포까지
  시도했다가 **로컬 전용으로 단순화**한 이력이 있음 (커밋: "Simplify back to local-only").
  다시 외부 배포를 시도하기 전에 이 이력을 먼저 확인할 것.
- 이 폴더는 이미 GitHub에 연결돼 있음 (아래 저장소). 다른 5개 프로젝트처럼 새로 만들 필요 없음.

## 저장소
https://github.com/mkleeeer/mk (기존 연결됨)

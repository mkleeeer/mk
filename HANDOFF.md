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
| 구글 드라이브 업로드 + 시트 연동 | `drive.py`, `sheets.py` — OAuth (`drive.file`, `spreadsheets` 스코프) |
| 실행 | `run.bat` |

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

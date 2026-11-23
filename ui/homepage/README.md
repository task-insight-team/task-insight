# ui/homepage — 발표용 소개 홈페이지

한빛센서텍(가상) 데이터와 우리 AI 업무 비서를 소개하는 **정적 홈페이지**입니다. 서버 없이 `index.html`을 브라우저로 열면 됩니다.

## 여는 법
- `index.html` 더블클릭, 또는 이 폴더에서 `py -m http.server 8765` 실행 후 http://localhost:8765
- 발표 모드: 키보드 → / 스페이스 = 다음 장면, ← = 이전 장면, 오른쪽 점 메뉴로 이동

## 장면
07:00 인트로 → 09:00 회사 소개 → 11:00 업무 150건(점) → 13:00 월별 타임라인 → 14:00 프로젝트 10개 → 15:00 예산 함정(7.03억 vs 109.03억) → 16:00 질문 7개 + 빠진 정보 → 18:00 Gemini 챗봇 vs 우리 AI

## 숫자와 글 고치는 곳
- 숫자: `index.html` 안의 `const DATA` 한 곳 (`data/kafe/` 기준으로 계산한 값)
- 문장: `const TEXT` 한 곳

## 분석 앱 연결 (iframe)
- `#ask` 섹션에 배포된 Streamlit 앱을 **iframe**으로 끼워 넣었습니다. 질문 입력 → 분석 계획 → [승인하고 실행] → 표·차트·근거는 모두 이 앱 안에서 동작합니다.
- 배포 앱 주소: https://task-insight-vojhzjrnxn7gnm7gkb8rgn.streamlit.app/
  - iframe `src`는 `…streamlit.app/?embed=true&embed_options=dark_theme`(홈페이지의 검은 바탕에 맞춘 다크 테마), 바로 아래 "화면이 보이지 않으면 새 창에서 열기" 링크는 `?embed=true` 없는 원래 주소(`target="_blank"`)입니다. 주소가 바뀌면 `index.html`의 `#ask` 섹션에서 두 곳을 고치세요.
- **앱이 잠들어 있으면 처음 열 때 30초~1분 걸립니다.** 발표 전에 한 번 열어 깨워 두세요.
- 왼쪽 질문 목록은 **발표용 질문 5개**입니다 (`TEXT.questions`). 누르면 처리 단계 6개가 차례로 켜지고, 그 질문을 **어떻게 처리하는지**(`how`)와 AI가 고르는 계산 도구 예(`tool`)가 설명으로 나온 뒤 "이 질문을 아래 앱에 입력해 보세요"로 이어집니다. 숫자·정답은 쓰지 않고, 결과는 오른쪽 앱에서만 나옵니다. 도구 이름은 예시이고 실제로는 AI가 고릅니다.
- 안내 문장은 `const TEXT`(`askLead`, `panelHint`, `tryIt`, `toolLead`, `embedFallback`, `questions`)에서 고칩니다.

## 정답은 넣지 않았습니다
- 이 페이지에는 분석 결과 숫자를 직접 쓰지 않습니다 (`ANSWERS`·`askAgent`는 없앴습니다). 화면의 분석 수치는 iframe 안의 앱(엔진 계산 결과)에서만 나옵니다.
- 빠진 정보 개수와 대결 점수도 `—`로 비워 두었습니다.
- API 키 등 비밀값은 이 폴더에 넣지 않습니다. 홈페이지에서 Gemini를 직접 부르지 않습니다.

## 다른 버전
- `drafts/v3_apple.html`: 이전 디자인(애플 스타일, 점 150개 스토리) 비교용. 정답은 이 파일도 비어 있습니다.

## 사용 도구 (CDN, 무료)
Pretendard, GSAP 3.12.5 + ScrollTrigger, Lenis 1.1.13, Three.js 0.160.0

## 이미지
`images/`의 사진 13장은 AI로 만든 가상 이미지입니다 (실제 회사·인물 아님).

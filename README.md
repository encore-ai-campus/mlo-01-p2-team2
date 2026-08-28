# MLO 1기 2차 프로젝트

## 1. 팀 소개

### 팀명

- 팀명: `mlo-01-p2-team2`

### 멤버

| 이름 | 역할 | GitHub |
|---|---|---|
| 강한솔 | 팀장 | [hansolsmart](https://github.com/hansolsmart) |
| 김건우 | 팀원 | [natural0448](https://github.com/natural0448) |
| 김세진 | 팀원 | [BSSM17](https://github.com/BSSM17) |
| 이여찬 | 팀원 | [Ducks-Lee](https://github.com/Ducks-Lee) |

## 2. 프로젝트 개요

### 프로젝트 명

- 퇴직 관리자 발생 시 대체인력 후보 추천 시스템

### 프로젝트 기간

- 2026년 8월 27일(목) ~ 8월 28일(금)

### 프로젝트 소개

- 퇴직 관리자가 담당하던 업무영역과 후보 관리자의 부서, 직위, 근속 정보를 활용하여 대체인력 후보를 검토할 수 있도록 지원하는 시스템이다.
- 현재 단계에서는 통합 레거시 원천을 Bronze에 보존하고 Silver 데이터로 표준화하여, 향후 추천 기능에 사용할 수 있는 신뢰 가능한 데이터 기반을 구축한다.

### 프로젝트 필요성(배경)

- 기존 인사·조직 데이터에는 식별자, 재직 상태, 조직 레벨, 날짜 형식이 일관되지 않아 관리자와 업무영역을 안정적으로 연결하기 어렵다.
- 원천 데이터의 계보와 복원 여부를 추적할 수 없으면 추천 후보의 선정 근거와 데이터 품질을 검증하기 어렵다.
- 원본을 변경 없이 보존하고 표준 데이터로 정규화하여, 대체인력 검토 과정의 신뢰성과 재현성을 확보하고자 한다.

### 프로젝트 목표

1. Bronze CSV의 고유 `source.record_id`를 기준으로 RAW_DB 복원율 95% 이상 달성
2. Bronze 원본·계보·SHA-256 보존 무결성 100% 달성
3. 직원, 업무영역, 상위영역 데이터의 컬럼명·타입·코드·날짜 형식 표준화
4. PK·FK·필수값·도메인·날짜 품질 검증과 오류 데이터 격리
5. 향후 대체인력 후보 추천에 활용할 수 있는 Silver 데이터 기반 구축

> Gold 계층, 추천 피처, 후보 점수 및 Top-K 구조는 추후 결정(TBD)로 보류한다.

### 내부 인사 요청 검토 MVP

후속 승인으로 현재 Silver RDB를 읽는 Django 내부 검토 화면을 구현했다. 이 MVP는 후보 점수나 Top-K 추천을 만들지 않고, 동일 상위 업무영역의 재직 관리자와 데이터 품질 근거를 조회하여 다음 네 상태를 안내한다.

- 내부 지속 검토 가능
- 일부 영역 내부 지속 검토 가능
- 내부 인력 근거 미확인
- 데이터 확인 전 판단 불가

전체 사업 규칙, 아키텍처, 파일별 역할, 실행 방법, 테스트 및 운영 전 차단사항은 [내부 인사 요청 검토 가이드 통합 구현 설명서](django/INTERNAL_HR_GUIDE_IMPLEMENTATION_GUIDE.md)를 기준으로 확인한다. 후속 개발자는 [인수인계 색인](django/HANDOFF_INDEX.md)에서 구현 파일 바로 옆에 배치된 개별 `*.HANDOFF.md`를 확인한다.

### 프로젝트 서비스 구현 기획

- `구글스프레드시트 링크` [인사데이터 정규화 서비스기획서](https://docs.google.com/spreadsheets/d/15OUUMSdnTXu12Z6qTOjmjGzySrY0geqHWaeLDEIrNHA/edit?gid=589290982#gid=589290982)

## 3. 기술 스택

| 구분 | 기술 |
|---|---|
| Language | Python |
| Data | MongoDB, MySQL |
| Data Format | CSV, JSON, YAML |
| Collaboration | GitHub, Google Sheets |
| Documentation | Markdown, Mermaid |


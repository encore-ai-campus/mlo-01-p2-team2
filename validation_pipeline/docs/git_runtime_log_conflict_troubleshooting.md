# Git 런타임 로그 변경으로 인한 브랜치 전환 오류 트러블슈팅

## 1. 문제 상황

서버를 실행한 상태에서 `develop` 브랜치로 이동하거나 최신 내용을 가져오려 했습니다. 그러나 Git이 다음과 같은 오류를 표시하며 작업을 중단했습니다.

```text
error: Your local changes to the following files would be overwritten by checkout
Please commit your changes or stash them before you switch branches.
```

이 오류는 현재 로컬 작업 폴더의 변경사항이 브랜치를 전환하는 과정에서 덮어써질 수 있기 때문에 발생합니다.

## 2. 원인 확인

먼저 현재 작업 폴더의 변경사항과 변경된 파일 목록을 확인했습니다.

```bash
git status
git diff --name-only
```

확인 결과 다음과 같은 파일들이 수정되어 있었습니다.

- `records.jsonl`
- `crawl_state.json`
- `pipeline.log`
- `validation.log`
- 기타 수집 상태 및 파이프라인 로그 파일

서버가 실행되면서 수집 결과와 로그 파일이 계속 변경되고 있었고, 해당 파일들이 Git에 이미 추적되고 있었습니다. 따라서 브랜치 변경이나 `pull` 과정에서 기존 로컬 데이터가 덮어써질 수 있었고, Git이 작업을 보호하기 위해 중단한 것입니다.

### 원인 흐름

```text
서버 실행
  ↓
로그·상태·수집 데이터 파일 변경
  ↓
Git 작업 폴더가 변경 상태가 됨
  ↓
checkout/pull 시 파일 덮어쓰기 가능성 발생
  ↓
Git이 작업 보호를 위해 중단
```

## 3. 임시 해결

### 로그를 보존해야 하는 경우

서버를 잠시 중지한 뒤 변경사항을 stash에 보관합니다.

```bash
git stash push -u -m "runtime logs backup"
```

이후 브랜치를 전환하거나 최신 내용을 가져올 수 있습니다.

### 로그가 불필요한 생성 파일인 경우

필요한 로그를 별도로 보관한 뒤 서버를 중지하고 해당 변경사항을 복구할 수 있습니다.

```bash
git restore django/log_lake/raw_data
```

> `git restore`는 해당 로컬 변경사항을 삭제하므로, 필요한 로그는 먼저 별도 저장해야 합니다.

## 4. 재발 방지

서버 실행 중 자동으로 생성되며 변경이 잦은 로그·상태 파일을 `.gitignore`에 추가합니다.

```gitignore
/django/log_lake/raw_data/*.jsonl
/django/log_lake/raw_data/*.log
/django/data/raw_data/records.jsonl
/django/data/raw_data/state/*
**/__pycache__/
*.py[cod]
```

### 이미 추적 중인 파일의 추적 해제

이미 Git이 추적 중인 파일은 `.gitignore`에 추가하는 것만으로는 관리 대상에서 제외되지 않습니다. 따라서 Git 인덱스에서 추적을 해제해야 합니다.

```bash
git rm -r --cached -- django/log_lake/raw_data
git rm --cached -- django/data/raw_data/records.jsonl
git rm -r --cached -- django/data/raw_data/state
git add .gitignore
git commit -m "chore: ignore runtime generated files"
```

`--cached` 옵션을 사용하면 로컬 파일 자체는 삭제하지 않고 Git의 추적만 해제할 수 있습니다.

> 원본 데이터처럼 보존과 공유가 필요한 파일은 무조건 무시하기보다 DB나 별도 저장소에서 관리하는 것이 적절합니다. `.gitignore`에는 소스 코드가 아닌 런타임 생성 파일만 등록해야 합니다.

## 5. 최종 정리

이번 문제를 통해 서버 실행 중 생성되는 로그와 상태 파일이 Git에 추적되고 있으면 브랜치 이동이나 `pull` 과정에서 오류가 발생할 수 있음을 확인했습니다.

앞으로는 다음과 같이 관리해야 합니다.

1. 서버 실행으로 생성되는 런타임 로그·상태 파일을 `.gitignore`에 등록합니다.
2. 이미 추적 중인 생성 파일은 `git rm --cached`로 Git 관리 대상에서 제외합니다.
3. 보존이 필요한 원본 데이터는 DB나 별도 저장소에서 관리합니다.
4. 브랜치 전환 전 `git status`로 변경사항을 확인합니다.

이를 통해 서버가 실행 중이어도 불필요한 로그 변경 때문에 브랜치 전환이나 최신 코드 반영이 차단되는 문제를 예방할 수 있습니다.

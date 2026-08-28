# `login.html` 인수인계

## 책임

승인된 사내 계정의 로그인 화면을 제공한다.

## Context·의존성

- Django `LoginView`의 기본 Form
- `second_project/base.html` 상속
- 오류 시 일반화된 아이디·비밀번호 확인 문구

## 변경 주의

계정 존재 여부를 구분하는 오류를 표시하지 않는다. 실제 비밀번호나 데모 자격증명을 템플릿에 하드코딩하지 않는다.


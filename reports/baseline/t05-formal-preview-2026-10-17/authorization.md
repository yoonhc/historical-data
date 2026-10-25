# 삭제 없는 정식 미리보기 범위

2026-10-17 사용자는 현재 설치 구성의 삭제 없는 정식 미리보기 진행과,
그 과정에서 지정 restic snapshot의 작은 checkpoint 근거 bundle을 읽어 대조하는 범위를 승인했다.
현재 목록·시각·보존 근거로 새 계획을 만들며 새벽의 9개 목록을 재사용하지 않는다.

설치된 `marketdata-checkpoint-retirement-preview.service`와 고정 T05 실행본을 사용한다.
미리보기는 계획·관측·명령 receipt를 기존 T05 control state에 기록한다.
checkpoint 자료와 registry를 재등록·삭제하거나 승인 문서를 활성화하지 않는다.
유지 사본 전체 내용 검사는 설치 절차의 실제 적용 직전 단계에 남긴다.
기존 자동 정리 네 mask, 비활성 recurring/manual, Chrony·두 supervisor를 유지한다.

실제 삭제, 반복 자동화, 신규 backup, 전체 restic 검사 재실행, 시장 자료 전체 복원,
시각·보존 정책 변경은 승인 범위에 없다.

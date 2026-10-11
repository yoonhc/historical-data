# T05 후보의 경계와 시각 근거

2026-10-11. 후보 구현·격리 검증 완료이며 운영 삭제·설치·timer 활성화 결과가 아니다.

## 승인된 순서

1. 기존 정리기를 재사용해 시각·삭제 안전 정책을 구현하고 격리 저장소에서 검증한다.
2. 시스템 systemd, `User=yoonhc`의 최종 실행 환경을 구성하고 별도 시험 유닛으로
   삭제 없는 미리보기를 검증한다. 운영 사용자 service/timer의 mask는 유지한다.
   시스템 운영 유닛은 아직 설치하지 않는다. 시험 유닛은 설치·실행·정리 범위가 고정된
   관리자 사전 점검으로 구분한다.
3. 실제 배포·첫 수동 삭제·timer 반복 삭제는 별도 승인 단계다. 수동 실행도 최종 시스템
   환경에서 정확한 계획 하나를 지정한다. 후보·정책·경로·실행 환경이 다른 과거 승인은
   재사용하지 않는다. 후보 0건은 실제 삭제 검증 완료가 아니다.

## 기존 근거를 훼손하지 않는 완료 상한

기존 `backup.json`의 `completed_at`에는 당시 UTC 오차 범위가 없다. 현재 Chrony가
정상이어도 과거 기록을 소급 인증하지 않는다. 원래 manifest·backup ID·archiver 원장을
수정하지 않고 별도 불변 근거를 추가한다.

기존 완료 manifest·입력 검증·restic snapshot·전체 읽기 검사·복구 근거의 연결을
기존 잠금 아래 확인한 뒤 현재 Chrony를 새로 조회한다. 이 관측의 UTC 상한을
**실제 완료가 이보다 늦을 수 없다는 상한**으로 쓴다. 원래 완료 시각을 복원한 값은 아니다.

`현재 관측 UTC 하한 - 완료 사본 확인 관측 UTC 상한 >= 72시간`

기존 최근 3개와 원래 72시간 보호에 위 조건을 추가한다. 첫 관측으로 증거를 얻은
사본은 최소 72시간 더 보호될 수 있다. 증거가 없거나 변조·불일치가 있으면 삭제하지 않는다.
원래 오래된 날짜·mtime·restic 시각만으로 이 추가 조건을 통과시키지 않는다.
완료 상한 파일은 checkpoint·manifest·repository/scope·snapshot에 연결하고 파일 해시로
고정한다. 계획은 사용한 정확한 근거와 후보·보호 목록을 포함한다.

이 방식은 기존 backup 실행 코드를 수정하지 않는다. 정리 평가에서 처음 확인하는
시점을 쓰므로 매일 평가하면 실제 완료보다 최대 하루 이상 보수적일 수 있고, 선행 작업이
미완료이면 더 늦어진다. 보유 기간을 줄이기 위한 정책 완화로 보상하지 않는다.

## 현재 시각과 실행 중 감시

Chrony의 조건부 오차 `abs(System time) + Root dispersion + max(0, Root delay)/2`와
조회 소요·샘플링 폭을 구분해서 기록한다. 이 상·하한은 시간원과 Chrony 모델 가정에
의존하며 절대 UTC 정확도 인증이 아니다.
[Chrony 4.5 tracking 문서](https://chrony-project.org/doc/4.5/chronyc.html).

- 현재 부팅의 root 소유 bootstrap profile·runtime profile·closed 준비 증거를 확인하고
  기존 coordinator 잠금을 읽기 전용 공유 방식으로 유지한다. ledger 수정·재등록은 없다.
- 실제 Chrony PID·invocation·명령행·설정 해시를 대조한다. `-x` 등 시각 제어가 꺼진
  provider를 운영 판단으로 받아들이지 않는다. localhost UDP `tracking` 조회만 사용하며
  제어 socket·makestep·시각/RTC 쓰기는 사용하지 않는다.
- reference 시각과 age는 기록하지만 600초 상한이나 `reference_stale`로 판단하지 않는다.
  미래 reference, 조회 실패/만료, 미동기화, 남은 보정 100ms·조건부 오차 500ms·skew 100ppm
  초과는 거부한다. 이것은 T05 전용 정책이며 다른 8개 업무/수집/Chrony 설정은 유지한다.
- 1초 UTC/monotonic/boottime 관측, 10초 provider 재조회, 조회 근거 수명 15초,
  표본 공백 5초·급변 250ms 조건을 사용한다. 이 수치는 초기 운영 정책이며 실측 최적값이 아니다.
- 사용 시점의 하한을 얻기 위해 오래된 관측에 경과 시간을 더하지 않는다. 최대 15초 이내의
  실제 관측 하한 자체를 사용한다. monotonic을 독립 UTC 정확도 증거로 취급하지 않는다.
- 계획·적용·장시간 검증 중에도 감시를 유지한다. 실제 rename/unlink/rmdir 직전에도
  현재 근거와 보호 기간을 재검사한다. 시각 이상·관측 기록 오류는 해당 실행에 고정한다.
  유한한 표본 사이의 모든 임의 시각 변경을 검출한다고 보장하지 않는다.

## 기존 정리와의 결합

기준은 `checkpoint-automation-79e29a3ee5152872`의 138개 고정 파일이다.
기존 무결성·복구·같은 backup ID의 당일 maintenance daily/restic 전체 검사·잠금 순서를
재사용한다. 여기서 daily는 데이터 갱신 T13이 아니라 checkpoint 생성 작업이다.

새 control state는 별도 경로이며 과거 정리 journal·plan은 읽어 합친다. 과거 성공·실패·
삭제 이력을 지우거나 archiver 상태를 덮어쓰지 않는다. 새 계획은 실행마다 새로운 ID를 가진다.
새 미완료 계획은 다음 자동 실행을 보류하고 점검 필요로 표시한다. 재개는 원래 계획과
새 명시적 수동 복구 승인을 연결하고 현재 부팅·시각·보호/저장/잠금 조건을 다시 확인한다.

상태는 `no_candidates`, `preview_ready`, `deferred`, `failed`, `complete`로 구분한다.
부분 삭제 뒤 중단은 실패와 미완료 journal로 남긴다. 수동 한 계획 승인의 종류와 자동 반복
승인의 종류를 구분하여 승인 파일을 복사해도 다른 용도로 통과하지 않게 한다.

## 시스템 실행 후보

시스템 정리 service/timer, 단일 계획 apply template, preview service를 별도로 렌더한다.
모두 `User=yoonhc`, 빈 capability 집합, `ProtectClock=yes`, `ProtectSystem=strict`,
`ProtectHome=read-only`를 사용한다. 외부 인터넷은 허용하지 않고 localhost 조회를 사용한다.
실제 정리에서만 승인된 checkpoint와 staging 경로에 쓰기를 허용한다. preview에서는
이 데이터 경로가 읽기 전용이다. 새 control state·보고서·cache와 기존 공통 lock 및 restic
lock 접점은 명시적으로 구분한다. 원본 데이터·catalog·restic 데이터 pack에는 쓰기를 주지 않는다.

15:40 KST, `Persistent=false`, `Restart=no`, 보류 후 다음 날 평가를 유지한다.
system manager의 현재 Job/Invocation/PID로 예약 출처를 검증한다. 기존 user manager 조회를
시스템 예약의 근거로 쓰지 않는다. 실행 계정의 Chrony 제어 권한을 확대하지 않는다.

## 현재 검증 범위

검사 중간 결과는 별도 XML에 보존하고, 최종 108개와 별도 재현 39개의 범위를 구분한다.
최종 소스·검사·배포 식별자는 [summary.md](summary.md)와 manifest에 고정했다.
운영 데이터의 삭제, 기존 grant 변경, 서비스 재시작, 시각/RTC/Chrony 변경,
`forget`/`prune`, 시험 공간 정리는 이 작업에 포함하지 않는다.

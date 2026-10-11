# 격리 검증과 한계

후보 `t05-completion-evidence-20261011-c9d413cfe11ec0e7`. **관련 215개 최종 통과, 핵심 58개 별도 재현 통과**.
운영 자료/시각에 대한 장애 주입은 없다.
기존 108개 결과를 이 보완의 검증으로 합산하지 않는다.

## 검사 범위

| 파일 묶음 | 사례 수 | 확인 내용 |
|---|---:|---|
| retirement_clock | 34 | U/관측 유효성, provider·부팅·불연속·만료, 승인된 anchor |
| checkpoint_evidence | 21 | 5초/72시간+60초 경계, 완료 후 관측, 중단·잠금·순서·복원·구형 도구 |
| checkpoint_registration | 2 | 읽기 전용 목록, 별도 승인·불변 기존 집합·첫 유효 근거·실패 후 보완 |
| clock_retirement | 4 | 근거 없는 보호, 실제 restic·삭제·독립 복원, 중단/재개, 변경된 근거/승인 거부 |
| backup/incremental/maintenance/wait | 46 | 기존 사본 생성·검증·복원·incremental 재사용·일일 업무·잠금 대기 |
| code/locks/retention/schedule | 31 | 고정 코드 신원·공통 잠금·기존 정리/예약 승인·중단 처리 |
| restic archive/schedule/trial | 77 | 실제 저장·전체 검사·reconcile·복구와 기존 예약 계약 |
| **계** | **215** | 최종 개별 결과는 아래 원본 XML과 보완 재실행을 함께 따른다 |

[candidate-tests.xml](candidate-tests.xml)은 전체 실행,
[exported-regression.xml](exported-regression.xml)은 아래 이식성 보완 후 해당 사례 재실행이다.
첫 전체 실행에는 Git commit 존재를 요구하는 구형 검사 한 건의 실패가 있었다.
고정 export는 `.git`이 없으므로 실제 계약인 전체 코드 식별자·소스 해시 일치를 검사하도록
**검사만 수정**했다. 이후 해당 사례는 **1개 통과(68.12초)**했다.
commit ID를 만들어 넣거나 코드/파일/승인 검사를 느슨하게 하지 않았다.
전체 실행은 **214개 통과·1개 실패(1,024.63초)**로 끝났고 보완한 1개까지 합해 215개 모두
통과했다. 결과를 덮어쓰지 않았다. [최종 개별 결과 집계](validation-result.json).
기존 fork 잠금 검사에서 Python 3.12의 multi-threaded fork 사용 경고 1건이 있었으며 검사는 통과했다.

검증 중 먼저 수행한 소규모 실행과 반복 실행은 별도 신규 사례로 합산하지 않는다.
초기 개발 과정에서 새 U 정책과 맞지 않던 기존 기대값, 호환 import 경로, 시험 코드 문제를
고친 기록도 [압축 실행 로그 목록](log-archives.json)에 보존한다. 첫 시도부터 모두 통과했다고 표현하지 않는다.

## 핵심 경계

- U=5초 허용, 1ns 초과 거부. 기존 개별 수치를 넘어도 유효한 U이면 허용한다.
  오래된 reference 자체는 거부하지 않고, 관측 상한을 넘는 future reference는 거부한다.
- query 정렬 여유와 sample 여유를 별도 보존하고 관측을 재계산해 변조를 거부한다.
  기간은 frozen lower 기준이며 72시간+60초 경계를 1ns 단위로 검사한다.
- observer가 호출될 때 실제 `backup.json`과 검증 가능한 데이터가 존재한다.
  이때 별도 프로세스의 입력 접근은 exclusive checkpoint 잠금으로 차단된다.
- 완료 marker 전/후, 불변 객체 발행 후 sidecar 전 중단을 구분한다.
  시각 조회 실패는 데이터 성공·생성 순서를 버리지 않으며, 최초 유효 사후 상한을
  반복 실행에서 바꾸지 않는다. registry 부재/경합도 정상 데이터 성공과 분리한다.
- 기존 manifest를 미래 시각으로 표시한 시험에서도 wall-clock 정렬로 최신 3개를 결정하지 않는다.
  실제 연결·최종 유지 3개가 필요하며 누락된 선행 객체·다른 manifest·중복 신원은 거부한다.
- 미리보기는 근거를 등록하지 않는다. 별도 inventory 승인·현재 목록 대조 뒤에만 등록하며
  정리 권한으로 전용 등록 승인을 대신할 수 없다. 다른 목록으로 baseline을 덮어쓰지 않는다.
- 긴 유지 사본 검증이 끝난 뒤 최초 변경 전에 새 관측이 오는 실행 순서를 확인했다.
  rename 뒤 41초 이동을 **가짜 표본으로 주입**한 시험에서는 unlink 0개로 중단하고,
  새 자동 계획은 보류한 채 별도 승인으로 기존 journal의 잔여 범위를 재개했다.

## 실제 restic·로컬 삭제 후 독립 복원

이 시험의 시각/provider/systemd 조회·승인 파일 소유권은 격리 대역이다.
데이터 사본·입력 잠금·불변 파일·실제 restic backup/check/restore·로컬 정리는 실제 실행했다.
운영 서비스나 시각을 변경한 시험은 아니다.

1. 기존 시험 사본 2개를 등록하고 이후 실제 새 사본 3개를 생성한다.
2. 새 sidecar를 실제 restic에 넣고 `check --read-data`를 통과한다.
3. 정확한 시험 계획으로 오래된 로컬 2개만 삭제하고 유지 3개와 원본/restic 불변을 대조한다.
4. T05 control state 전체를 다른 경로로 분리한다.
5. **명시적 snapshot ID**에서 별도 경로에 복원한 sidecar만으로 완료 상한과 선후관계를 읽는다.
6. 원래 선택·근거와 일치하며 남은 3개에 대한 다음 선택은 후보 0개임을 확인한다.

[원본 근거](isolated-restored-evidence.json)와 [대조 결과](isolated-restore-result.json)에
snapshot ID, backup ID, sidecar 해시, 전체 읽기 검사, 삭제/유지 ID와 상한을 보존했다.
완료 상한·순서 근거 복원은 삭제 승인·진행 journal 복원과 다르다. 후자는 자동 활성화하지 않는다.

## 별도 경로 재현·구형 도구 호환

[reproduce.py](reproduce.py)는 source.tar.gz의 151개 목록과 해시를 확인하고 새 경로에 풀었다.
`uv sync --offline --frozen --no-editable --link-mode copy`로 환경을 구성했다.
**58개 검사 통과(82.59초)**: 시각 34·근거 21·등록 2·실제 삭제/독립 복원 1개다.
이는 동일 사례 재현이며 215개에 더하지 않는다.
[reproduced-tests.xml](reproduced-tests.xml), [환경/import](reproduced-environment.json),
[결과](reproduction-result.json)을 함께 보존한다.

`python -I`에서 실제 설치된 backup/checkpoint_clock/checkpoint_evidence 모듈이
재현 경로의 site-packages에서 import되고 후보 소스 해시와 같음을 확인했다.
실제 제공할 wrapper는 고정된 release 내부 source를 명시적으로 선택한다.
개발 작업 폴더와 혼합한 import로 확인한 결과가 아니다.

이전 동결 source의 backup/restore를 별도 `-I` 프로세스에서 사용해 새 sidecar가 있는 사본을
검증·복원하고, 복원 저장소에 추가 자료를 저장해 새 backup을 만드는 호환 시험도 통과했다.
구형 도구가 새 sidecar를 의미적으로 해석한다는 보장은 아니다. 새 조회 CLI와 원래 sidecar를 보존한다.

## 최종 실행 환경에 남는 확인

이 절은 격리 검증 당시의 남은 범위다. 이후 15:50 시스템 미리보기와 17:24까지의 설치·실효 명령 확인은
[운영 전환 상태](../t05-evidence-deploy-2026-10-11/status.md)에 따로 기록했다. 새 정기 근거·restic 보존/복원은 여전히 미확인이다.

[네 시스템 유닛 비교](unit-comparison.json)는 이전 접근 시험 유닛과 release 경로 외에 같고,
두 새 user drop-in은 ExecStart만 교체하며 ExecStartPre와 timer를 재설정하지 않는다.
이는 실제 설치 경로의 systemd 접근·종료 동작을 새로 확인한 결과가 아니다.

기존 root 접근 시험은 성공했지만 당일 백업 미완료로 전체 미리보기가 보류된 상태였다.
이 후보는 운영에 설치하지 않았으며, 최종 /opt·/etc·/var/lib 소유권/권한과 실효 유닛,
첫 운영 완료 근거 생성·restic 편입·복원은 별도 단계로 남아 있다.
짧은 격리 시험을 장기 로그 크기·I/O 성능이나 실제 시간원 정확도의 인증으로 확대하지 않는다.
불변 증거는 작은 파일이지만 이력과 bundle이 누적된다. 관리 상태/백업 비용은 운영 후 관측 대상이다.

# 2026-10-05 대용량 시험 산출물 정리 미리보기

이 문서는 시험 저장소를 남기는 **초기안**이다. 이후 사용자와 범위를 검토해
[시험 저장소·캐시까지 포함한 197.34 GB 확장안](../trial-artifact-retention-expanded-2026-10-05/summary.md)을 만들었다.
현재 검토 대상은 확장안이며, 아래 초기 계획·측정·원본 증거는 당시 기록으로 보존한다. 두 안 모두 실제 삭제 전이다.

**추출본·복원본·재백업본 12개 디렉터리만 수동 정리 후보로 지정했다. 예상 회수량은
172,000,382,976바이트(172.00 GB, 약 160.19 GiB)이며, 실제 삭제·회수는 0이다.**
이 보고서는 삭제 승인이 아니다. 기존 checkpoint 자동 정리의 반복 권한도 이 목록에 적용하지 않는다.
서비스·운영 코드·정책·설정은 변경하지 않았다.

- [정확한 후보·보호 목록과 전체 계획](plan.json)
- 전체 계획 ID: `17df37a1984fd85ff73e166a8483fdff14ea146ac058ee3ca33766ddc85838f4`
- 기준 commit: `ae6b62019e109f485ad9541a6bb6a2cd906a98ee`
- 조사: 10/5 20:34–20:35 KST 파일 목록, 20:41 KST restic·사용 여부,
  20:45 KST 실제 호스트 마운트·설정 후속 확인. 계획·검증 파일에는 각각 정확한 UTC 시각을 남겼다.
- [파일 조사](inventory.json), [restic·서비스 조회](probe.json),
  [호스트 후속 확인](host-followup.json), [계획·용량 검산](validation.json).

## 후보와 보존 대상

각 시험의 **extracted / store / new-backup** 세 디렉터리만 후보다.
GB는 십진 단위이며, 일반 파일과 디렉터리에 할당된 블록을 합했다.

| 시험 | 후보 수 | 예상 회수량 | 남기는 재생성 근거 |
|---|---:|---:|---|
| 9/26 pilot | 3 | 26.75 GB | pilot 저장소의 `0ba86959…`, 기존 시험 기록 |
| 9/27 pilot | 3 | 32.28 GB | pilot 저장소의 `f0dbb406…`, 기존 시험 기록 |
| 9/28 운영 snapshot 복원 | 3 | 38.46 GB | 운영 저장소의 `7ddea524…`, 복원·조회·재백업 기록 |
| 10/3 독립 복구 | 3 | 74.51 GB | 보존한 복제 저장소와 운영 저장소의 `d27a0a55…`, 복구 키트·검증 기록 |
| **합계** | **12** | **172.00 GB** | 모든 원래 restic 복구 지점 유지 |

정확한 후보 경로는 다음과 같다. 상위 폴더에 대한 재귀 삭제나 와일드카드로 확대하지 않는다.

```text
/mnt/backup/marketdata-restic-pilot-work/2026-09-26-extracted
/mnt/backup/marketdata-restic-pilot-work/2026-09-26-store
/mnt/backup/marketdata-restic-pilot-work/2026-09-26-new-backup
/mnt/backup/marketdata-restic-pilot-work/2026-09-27-extracted
/mnt/backup/marketdata-restic-pilot-work/2026-09-27-store
/mnt/backup/marketdata-restic-pilot-work/2026-09-27-new-backup
/mnt/backup/marketdata-restic-restore/0cc942c867b34ba1b561fc4f7e6d1dca/extracted
/mnt/backup/marketdata-restic-restore/0cc942c867b34ba1b561fc4f7e6d1dca/store
/mnt/backup/marketdata-restic-restore/0cc942c867b34ba1b561fc4f7e6d1dca/new-backup
/mnt/backup/marketdata-independent-recovery-20261003/restore-work/7c0d0db1838f4a3f8ea81d8ae6365a07/extracted
/mnt/backup/marketdata-independent-recovery-20261003/restore-work/7c0d0db1838f4a3f8ea81d8ae6365a07/store
/mnt/backup/marketdata-independent-recovery-20261003/restore-work/7c0d0db1838f4a3f8ea81d8ae6365a07/new-backup
```

다음 항목은 그대로 보존한다.

- 원본 시장 데이터·모든 revision, live catalog, 작업용 checkpoint와 편입 전 사본.
- `/mnt/backup/marketdata-restic` 운영 저장소와 모든 snapshot.
- `/mnt/backup/marketdata-restic-pilot`와
  `/mnt/backup/marketdata-independent-recovery-20261003/repository` 시험 저장소 두 개.
- `/mnt/backup/marketdata-recovery-kit-20261003` 전체. 암호·코드·패키지·검증 자료를 포함한다.
- pilot의 `cache`(약 20.13 MB), 상위 작업 폴더, 설정·처리 상태·기존 기록.

## 현재 확인한 근거

1. **파일 448,727개와 디렉터리 389,220개를 조사했다.** 모든 일반 파일의 하드링크 수는 1이다.
   후보 밖 하드링크로 유지될 블록은 발견되지 않았다. 심볼릭 링크·특수 파일·다른 장치·후보 내부
   마운트가 없고, manifest 대비 누락·예상 외 파일·크기 차이·설명 없는 빈 디렉터리도 없었다.
   복원 저장소의 `restore.json`, `state/writer.lock`, `state/file-layout.lock`은 별도 허용 목록으로 구분했다.
2. 파일 자체의 논리 크기 합계는 **169,316,748,819바이트(169.32 GB)**다. 예상 회수 172.00 GB에는
   파일시스템의 할당 단위와 디렉터리 블록을 포함한다. 별도 `du -sc -x -B1` 결과도 폴더별·합계가
   동일했다. 이전의 172.02 GB에는 이번 범위에서 뺀 캐시와 상위 디렉터리가 포함돼 있었다.
   열린 파일 참조·동시 쓰기·파일시스템 내부 비용이 있으므로 실제 여유 공간 증가는 별도로 관측해야 한다.
3. 실제 호스트의 `/mnt/backup`은 ext4, UUID
   `d90aaf81-2358-411f-8022-4e1a3f7b0973`로 일치했다. pilot 저장소 ID는 과거 `init.jsonl`의
   `3cb78902823a4335c5a9fc3ab5635f583c4b4080a8273422a0defdbe3f11effc`,
   운영·복제 저장소 ID는 기존 `b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558`과 일치했다.
4. 네 시험 모두 정확한 restic ID로 `/backup.json`을 읽어 추출본과 **바이트 단위 일치**를 확인했다.
   snapshot 목록만 보고 추출 가능성을 추정하지 않았다. 독립 복제 저장소는 보존 중인 키트 암호로 읽었다.
   암호 값이나 해시는 기록하지 않았다. 원래 manifest의 backup ID·source root도 유지했다.
5. 각 시험의 추출본과 새 독립 백업은 manifest의 **전체 파일 경로·크기·SHA-256·종류 및 카탈로그
   집계가 동일**했다. 기존 검증 성공·조회 일치·파일 독립성 기록과 두 backup ID를 다시 연결했다.
   이번에 약 172 GB의 실제 내용 해시를 다시 읽거나 전체 복원 시험을 반복한 것은 아니다.
   운영 두 지점은 10/5의 기존 `check --read-data` 성공 범위에도 포함돼 있다.

| 시험 | 실제 보존 확인한 전체 snapshot ID |
|---|---|
| 9/26 pilot | `0ba869592143e008150fce764cb9eb742711337cebc8c48aa6bfd282d61862d3` |
| 9/27 pilot | `f0dbb406cc407fc93ddd815b4e5ab8db046278c8301744b9c5c2af7d891795d8` |
| 9/28 운영 | `7ddea5243f9262eb058b15e97df1976eb5c369de386010c736b449a10a250713` |
| 10/3 독립 복구 | `d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad` |

## 증거 보존과 재생성의 의미

후보 안의 **backup.json 8개와 restore.json 4개**를 바이트 그대로 압축 복사하고, 복사본의 압축 해제
결과와 원본을 대조했다. 압축 합계 19,364,949바이트다. 전체 파일 메타데이터 목록도
31,290,503바이트의 압축 기록으로 남겼다. 둘을 합한 약 50.66 MB는 후보 밖의
`reports/trial-artifact-retention-2026-10-05/`에 보존한다. 원본·압축본·목록의 SHA-256은
`inventory.json`에 있다. 이 큰 파일들은 로컬 보고서이며 Git 추적 대상은 아니다.
기존 baseline 보고서·실행 로그·복구 receipt도 그대로 보존한다.

원래 데이터를 다시 얻을 때는 표의 **명시적 snapshot**을 새 경로로 추출하고, 프로그램 복원 절차를
사용한다. 새 독립 백업은 새 전용 목적지에서 생성한다. 과거의 복원 실행·생성 시각·새 backup ID까지
똑같이 재현한다는 뜻은 아니다. 삭제 후에는 당시 물리 사본의 직접 재검증이 불가능하므로,
남긴 manifest·검증 기록과 새 복원 검증을 구분해야 한다.

10/3 시험의 manifest·receipt에는 격리 환경의 `/mnt/backup/restore-work/...`가 기록돼 있고,
실제 호스트 후보는 `/mnt/backup/marketdata-independent-recovery-20261003/restore-work/...`다.
기존 [경로 대응 기록](../restic-transition-2026-10-03/review.json)과 일치하며 출처 필드를 수정하지 않았다.
9/27 원래 작업용 checkpoint는 이전에 승인한 정리로 없어졌으므로, 옛 pilot 설정을 그대로 다시 실행하는
방식은 재생성 절차가 아니다. 남긴 restic 지점과 새로운 작업 경로를 사용해야 한다.

## 운영과 사용 여부

관측 당시 aggTrade·호가 서비스는 실행 중이고 기존 타이머들은 활성 상태였다.
같은 사용자 프로세스 156개를 두 번 조사했을 때 열려 있는 파일·cwd·메모리 매핑에서 후보 참조는
보이지 않았다. 다만 `systemd`·`(sd-pam)` 두 프로세스의 일부 접근은 제한됐고, 다른 사용자나
관측 이후 시작한 작업까지 확인한 것은 아니다. **이 관측은 배타 잠금이나 미래 삭제 시점의 사용 없음 보증이 아니다.**

운영 설정은 `marketdata-restic-restore` 상위 작업 폴더를 사용하며 이 폴더는 남긴다.
새 복원은 `Archive.extract_verify()`에서 새 UUID 디렉터리를 만들므로 기존 시험 출력이 필요하지 않다.
pilot 설정의 상위 폴더·저장소도 유지한다. 조사 전후 운영 설정 해시는 동일했다.

현재 자동 checkpoint 정리기의 `recovery_guard()`는 보존한 복구 receipt의 SHA-256·성공 결과와
실제 운영 snapshot·backup 연결을 검사한다. 세 대용량 시험 폴더의 존속을 요구하지 않는다.
receipt SHA-256 `8a3adfe4de68c1a0fcca1fcbe6d184b62638b2da25df217d549daf438e4b5909`는
승인한 정책과 일치했고 운영의 보호 지점 두 개도 남아 있다. 과거 receipt의 출력 경로는 역사적 기록으로
유지하고, 나중에 삭제한다면 별도 정리 결과 기록을 추가한다. 기존 상태 파일을 고쳐 통과시키지 않는다.

## 적용 전 남은 단계

**10/6 15:40 KST 첫 자동 checkpoint 정리 결과를 먼저 확인한 뒤, 이 수동 계획을 최종 재대조하는
순서를 추천한다.** 20:41 조회에서 타이머의 LastTrigger와 서비스 시작 시각은 비어 있었다.
이번 계획 작성이나 삭제 없는 조회를 첫 예약 완료로 계산하지 않았다.

1. 첫 예약의 실제 결과와 미해결 오류를 확인한다. 시각이 지났다는 이유만으로 완료를 가정하지 않는다.
2. 적용 직전 UUID·세 저장소 ID·정확한 snapshot·manifest·복구 기록과 후보 목록을 다시 대조한다.
   inode·파일 목록·크기·시각·하드링크·마운트 등 변경이 있으면 옛 목록으로 진행하지 않는다.
3. 해당 경로를 읽거나 쓰는 복원·백업·수동 작업이 없음을 확인하고, 사용하는 기존 협력적 잠금과
   충돌하지 않도록 한다. 확인할 수 없는 사용은 해소한 뒤 진행한다. 수집 전체 재시작은 필요하지 않다.
4. 정확한 최종 계획에 별도 수동 삭제 승인을 연결한다. 이번 보고서에는 삭제 실행 기능이 없다.
   실제 적용은 대상별 진행·중단 기록과 파일 목록을 확인하는 방식으로 준비하며 상위 폴더 전체를 지우지 않는다.
5. 적용한다면 삭제 결과·예상 회수량·같은 파일시스템 여유 공간 전후 변화·보호 대상 보존을 별도로 기록한다.
   `forget`·`prune`, 시험 restic 저장소 삭제, 자동 checkpoint 권한 확대는 포함하지 않는다.

현재 완료한 것은 **분류·재생성 근거 대조·증거 복사·삭제 없는 계획**이다.
삭제 승인·실제 삭제, 첫 자동 정리와 그 뒤 다음 daily/restic 연결 관찰은 아직 완료하지 않았다.

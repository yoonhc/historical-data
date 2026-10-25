# T18 일시 정지 완료와 제한된 공간 확보 승인안

이 문서는 승인 당시의 고정 범위다. **별도 승인 후 10/19 22:59에 정확한 1,952개 처리·약 15.45GB 공간 확보를 완료**했다. 완료된 명령을 다시 실행하지 않는다. 현재 수집/pack 재개와 10/21 보관 지연·남은 범위는 [최신 상태](status.md)에 기록한다.

## 이번 작업창의 timer 일시 정지 — 완료

사용자가 승인한 `sudo /usr/bin/systemctl stop marketdata-restic-archive.timer`를 터미널에서 실행했다. 실효 의존관계에서 다른 업무로의 중지 전파가 없고 archiver/job이 유휴임을 먼저 확인했다. 사후 대조에서도 **timer inactive·다음 예약 없음·enable 유지**, **archiver inactive/MainPID=0·ControlPID=0·Job 없음**이다. 마지막 예약은 10/19 22:35 KST, archiver invocation은 전후 동일하다.

유닛·enable 링크·프로필·시각 binding·Chrony 설정·T05 recurring/manual의 관측 파일과 archive ledger 해시가 전후 일치한다. enable/disable·mask·daemon-reload·service stop/start는 실행하지 않았다. [정지 결과](timer-stop-result.json), [사전 의존관계](timer-before-systemd.txt), [사후 관리자 상태](timer-after-systemd.txt), [정지 journal](timer-stop-journal.jsonl).

점검 당시 편입된 로컬 checkpoint 세 개(10/17·18·19)는 모두 restic snapshot과 연결되고 미보관 편입 사본은 **0개**다. 편입 전 제외 사본 세 개는 그대로다. 앞으로 생기는 사본은 timer 재개 전까지 보관이 지연될 수 있다. `enabled`를 유지한 작업창의 일시 정지이므로 재부팅 뒤까지 막는 설정으로 해석하지 않는다. timer 재개는 아직 승인되지 않았다.

## 제한된 공간 확보 — 승인 대기

Operation:

```text
d8d3bf23bc43518e41928294779c9ee5f5f1de3955527937be262609ab80f650
```

정확한 목록 plan:

```text
0ea1f22baf75a483329fcfec350b56321245ef6b2f0000ed39a9a3bdc04c9be2
```

| 항목 | 고정 범위 |
|---|---|
| 대상 | `reports/restic-archive-runs/<run>/<command>.stdout`의 두 snapshot manifest 출력 |
| 선택 조건 | 10/18 09:00 KST 이전 정상 종료한 run, `dump <snapshot> /backup.json` exit 0 |
| 직접 검증 | **1,956개**, 각 파일을 실제 바이트 비교하고 archive ledger의 기대 SHA-256과 일치 확인 |
| 유지 | snapshot·UID/GID·0600/0664·xattr별 원본 **4개** |
| 변경 | 나머지 **1,952개 경로**를 같은 내용·권한의 원본에 대한 하드링크로 원자 교체 |
| 예상 회수 | **15,450,640,384바이트 = 15.45GB / 14.39GiB** |
| 처리 후 예상 가용 | 점검 약 7.54GB에서 **약 22.99GB / 21.41GiB**. 동시 작업 때문에 실측은 달라질 수 있음 |
| 임시 공간 | 큰 본문 복사 **0바이트**. 한 번에 임시 하드링크 한 개, 계획 외 실행 감사 메타데이터 예산 **8MiB 이내** |
| 서비스 제어 | 없음. timer 재개·수집기/pack start/reset-failed는 이 operation에 포함하지 않음 |

두 snapshot은 `726fe603…`와 `7ddea524…`다. 전체 경로·원래 inode·할당량·권한·시각·해시·원본/receipt 연결은 [dedup-plan.json](dedup-plan.json)에 고정했다. 본문 사본은 추가로 만들지 않았다. [도구와 보호 입력 해시](dedup-operation.json), [적용 도구](deduplicate.py).

### 보존과 변경의 구분

모든 원래 경로와 파일 내용·해시·UID/GID·mode·xattr를 유지한다. command/run receipt, request, archive ledger, T05 근거 및 실제 checkpoint/restic 자료는 변경하지 않는다. 두 권한 유형은 섞지 않는다.

대상 **inode·link 수·ctime은 바뀌며 mtime은 같은 권한의 유지 원본 값으로 공유**된다. 원래 값은 고정 목록에 보존되고 실제 조회 시각은 변경하지 않는 실행 receipt에 남는다. inode/개별 mtime까지 보존하는 방법으로 표현하지 않는다.

현재 archiver는 UUID별 `open('xb')`로 새 출력을 쓰고 완료된 출력을 다시 열어 수정하지 않는다. 설치 T16 request 검증은 파일 바이트/해시를 사용하며, 실제 설치 계약 모듈로 두 권한 유형의 하드링크 전환 뒤 기존 요청 판정이 유지됨을 시험했다. T05의 inode/tree inventory는 checkpoint 경로에 적용되고 이번 report 출력은 그 대상이 아니다. 과거 모든 임의 외부 도구에 대한 일반적 호환성을 주장하지 않는다.

격리 검사 **12개**에서 경로/내용/권한 보존, 반복 적용, 기록 실패, 임시 링크 단계 중단 후 재개, 내용/권한/심볼릭 링크/미승인 추가 하드링크 변경 거부를 확인했다. [설치 계약 호환 결과](hardlink-compatibility.json), [검사 코드](test_deduplicate.py).

22:54 KST 고정 도구의 **운영 읽기 전용 check 통과**, 교체 **0개**다. 전체 대상 바이트·receipt·현재 보호 입력·같은 boot·timer 정지·archiver 유휴를 대조했다. [check 원본](dedup-audit/6722acb72dc14d319f3e982aa617ff9d.json).

승인 후 적용도 같은 검사를 다시 하고 기존 archive 잠금이 경합하면 중단한다. 알 수 없는 변경을 덮어쓰지 않는다. 중단 시 이미 완료한 링크와 원래 파일을 구분하고, 같은 고정 operation 안에서만 재개한다. 기존 경로를 복구하기 위해 대형 사본을 다시 만드는 절차는 없다.

승인 후 실행할 정확한 명령(UID 1000, sudo 없음):

```sh
/usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t18-capacity-recovery-2026-10-19/deduplicate.py --action apply --approve-operation d8d3bf23bc43518e41928294779c9ee5f5f1de3955527937be262609ab80f650
```

## 공간 확보 직후 복구 범위

다른 출력 전체 정리나 새 보관 후보 배포를 기다리지 않고 `/home`의 실제 가용량과 기존 보호 기준을 대조한다. 우선 두 collector가 이미 자동 재시도 중이면 **추가 start/reset-failed 없이** 새 child·원문 수신/저장·새 시각 근거를 확인한다. 시각 gate가 보류하면 기존 사유를 유지하고 성공으로 표시하지 않는다.

현재 pack은 용량 오류 뒤 시작 제한 상태다. 충분한 공간 확보 후 최신 journal·Job/PID·StartLimitHit·실효 Restart를 다시 확인한다. 자동 요청/실행이 있으면 추가 시작하지 않는다. 실제 제한 때문에 멈췄고 별도 실행 승인을 받은 경우에만 **`marketdata-pack.service` 한 개**의 `reset-failed`(필요할 때만)와 한 번의 `start --no-block`을 사용한다. collector도 자동 재시도가 사라져 실제 수동 복구가 필요한 경우에는 해당 정확한 유닛만 별도 범위로 다룬다. 전체 reset-failed·원장/예산 초기화·보호 기준 완화는 하지 않는다.

현재 수집 재개 확인과 pack 후속 처리·interrupted collection·과거 공백 대조는 별도로 기록한다. 오래된 `latest.json`이나 systemd의 시작/성공만으로 복구 완료를 선언하지 않는다.

## Archiver 재개와 첫 재발 방지 수정

이번 공간 확보 operation도 timer 재개를 허용하지 않는다. 미보관 checkpoint·미완료 요청·검사 상태를 계속 읽기 전용으로 기록한다. 재개 전에는 기존 조회·검증을 유지하면서 성공한 동일 본문의 중복 영구 보관만 없애는 후보를 검증하고, 정확한 설치/전환 범위를 별도로 검토한다. `OnActiveSec=5s`와 `Persistent=true`의 활성화 직후 요청도 재개 계획에 포함한다.

첫 수정에서 처리 0건 반복의 실제 저장량 증가, 신규/실패/불일치/중단·receipt/요청 복구를 검사한다. 30분 조회 주기, 전체 읽기 검사, T05 정리 조건은 바꾸지 않는다. 현재 운영 코드를 수정하거나 archiver를 재개하지 않았다.

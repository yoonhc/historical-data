# restic 별도 타이머 적용 — 2026-09-27

승인된 병행 운영 타이머를 설치·활성화하고 실제 사용자 서비스 환경의 실행을 확인했다.
**타이머 적용은 완료, 날짜별 새 사본 3개 관찰과 새 운영 snapshot 복원은 아직 미완료**다.
기존 daily·수집·원본·기존 하드링크 백업·시험 사본을 대체하거나 삭제하지 않았다.

- [운영·관찰·롤백 안내](../../../docs/restic-scheduled-operations.md)
- [설치/실행/테스트 근거](results.json), [사용자 서비스 journal](journal.jsonl), [테스트](tests.xml)

## 설치 전 확인

| 항목 | 실측 결과 |
|---|---|
| systemd 유닛 구문 | 사용자 모드 verify 통과, service/timer 두 파일 |
| 사용자 | `yoonhc`, UID 1000. 사용자 관리자 소유권과 서비스 내부 UID 일치 |
| 로그인 독립 설정 | `Linger=yes`, `user@1000.service` active. 재부팅·로그아웃 시험 없음 |
| Python | 프로젝트 `.venv/bin/python`, 실제 인터프리터 `/usr/bin/python3.12` |
| restic | `.cache/restic/0.19.1/restic`, 0.19.1 및 기존 고정 바이너리 SHA-256 일치 |
| 프로필·암호 접근 | 사용자 소유 0600 파일, 해당 사용자 접근 및 실제 저장소 열기 성공 |
| 필수 마운트 | `/mnt/backup`, ext4 UUID `d90aaf81-2358-411f-8022-4e1a3f7b0973` 일치 |
| 저장소 ID | `b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558` 일치 |
| 호스트별 경로 | `~/.config/marketdata/restic-service.env`에 Python·프로필·모듈 경로 분리 |

새 유닛만 `~/.config/systemd/user/`에 설치했다. 기존 사용자 유닛 14개와 완료 사본 manifest 4개의
해시가 적용 전후 동일했다. 데이터 패키지나 기존 수집·daily 유닛 소스는 변경하지 않았다.
설정 파일과 암호 값은 Git에 추가하지 않았다.

## 실제 서비스·타이머 실행

| 구분 | 실행 기록 | 관찰 날짜 집계 |
|---|---|---|
| 설치 후 수동 서비스 시험 | 22:07:28~22:07:32 KST, 서비스 내부 사용자·PID·실행 ID 대조 성공, 처리 0개 | 제외 |
| 첫 실제 타이머 실행 | **22:08:37~22:08:41 KST**, systemd `Result=success`, `ExecMainStatus=0`, 처리 0개 | 제외 |
| 다음 정규 예약 | **2026-09-27 22:35 KST** (기록 시점) | 아직 발생하지 않음 |

첫 실제 타이머 서비스의 invocation ID는 `d0c4c20a9b814644a7f9537995289f03`이다.
`TRIGGER_UNIT=marketdata-restic-archive.timer`, realtime/monotonic trigger 값과
실제 타이머 D-Bus 속성, 서비스의 invocation/PID가 모두 일치했다. 단순히 서비스가 실행됐다는 이유로
예약 실행이라고 추정한 것이 아니다. 앞선 수동 서비스 시험에는 timer trigger가 없어서 별도로 분류했다.

타이머는 **enabled·active(waiting)** 상태다. 활성화 5초 뒤 첫 탐색 후 매시 **UTC 05분·35분**에
탐색한다(KST도 05분·35분). `Persistent=true`, 정확도 1초다. 서비스는 oneshot이므로 성공 후
inactive(dead)로 돌아온다. nice 10·I/O best-effort 7·6시간 제한·SIGINT·120초 정리 대기를 실제
유닛 속성에서 확인했다.

두 실행 모두 기존 snapshot `726fe6039418d328006f11146c44e188560b3a4f4aff39ef9b87620abeedb8a1`을
확인했으며 새 저장이나 전체 검사를 수행한 것으로 집계하지 않았다. 보관 상태는 정상·대기 0개였고,
기존 aggTrade·depth 서비스도 active였다. 이 실행은 오늘 수동 저장 성공을 날짜별 관찰로 다시 센 것이 아니다.

## 아직 미완료인 관찰과 자동 기록

관찰 시작은 **2026-09-27 22:07:27.772948 KST**다. 현재 결과는 다음과 같다.

| 관찰 항목 | 현재 상태 |
|---|---|
| 시작 이후 생성된 서로 다른 UTC checkpoint 날짜의 새 사본 저장·전체 검사 | **0/3** |
| 위 사본 중 명시적 전체 ID로 선택한 새 운영 snapshot 복원·조회·독립 재백업 검증 | **대기** |
| 전체 관찰 완료 | **아니오** |

`restic_schedule.py`는 검증된 타이머 실행에서 새 저장과 같은 실행의 `check --read-data`가 성공한
사본만 날짜별로 기록한다. 수동 실행·처리 0개·기존 검사 영수증·모호한 실행 출처·같은 날짜의 반복은
3개 날짜 집계에서 제외한다. checkpoint UTC 날짜와 실제 업로드/검사 날짜를 별도로 남긴다.

첫 집계 대상 새 사본의 **64자리 snapshot ID를 먼저 기록한 뒤**, 독립 추출 → 프로그램 복원 →
전체 무결성·조회 대조 → 새 전용 독립 백업 → 새 백업 검증을 자동으로 한 번 수행하도록 연결했다.
아직 그 입력이 생성되지 않았으므로 성공 결과나 미래 날짜 행을 미리 채우지 않았다.
실패·중단 시 자동 복원 재시도를 반복하지 않고 증거·작업 공간을 보존한다. 나머지 병행 보관은 계속된다.

관찰 ledger는 원래 archive 상태와 분리되어 있고, 날짜별 증거는 backup ID·snapshot ID·systemd
실행 ID·실제 archive 실행/검사 영수증에 연결된다. 날짜 3개와 새 사본 복원이 모두 통과해야 관찰 완료다.
날짜별 결과는 **앞으로 실제로 발생한 예약 기록**으로만 추가된다.

## 테스트와 적용 범위

새 예약·집계·복원 연결 검사 **22개 통과, 실패·건너뜀 0개**. 수동/모호한 trigger·처리 0개·이전
사본·이전 검사·검사 실패·불완전 저장 제외, 날짜 중복 방지, 세 날짜와 복원 상태 분리, 중단 후 복원
영수증 연결, 초기화 덮어쓰기 거부, 자동 복원 반복 방지를 확인했다. 작은 실제 restic 저장소에서
보관→전체 검사→명시적 ID 복원→조회·독립 재백업과 다음 실행의 무중복 동작도 통과했다.
기존 보관 도구와 데이터 패키지는 변경하지 않았다. 이전 수동 구현 단계의 70개 검사 결과와 구분한다.

실제 새 날짜의 대용량 복원은 아직 수행하지 않았다. 현재 테스트·기동 성공은 장기 관찰이나
Proxmox VM 이전 검증, 기존 백업 대체, 전체 디스크 공간 절감을 완료했다는 뜻이 아니다.

## 상태 조회와 새 예약만 중지

프로젝트 루트에서 현재 보관·관찰 상태를 조회한다.

```bash
.venv/bin/python -m scripts.restic_schedule \
  --profile /home/yoonhc/.config/marketdata/restic-archive.toml status
systemctl --user list-timers marketdata-restic-archive.timer --all
```

새 예약만 중지하는 롤백은 다음 한 명령이다.

```bash
systemctl --user disable --now marketdata-restic-archive.timer
```

기존 daily·수집은 계속되고 사본·상태·설정은 보존된다. 이미 진행 중인 restic 서비스는 현재 작업을
끝까지 수행한다. 이번 적용 검증에서 롤백·재부팅·로그아웃은 실행하지 않았다.

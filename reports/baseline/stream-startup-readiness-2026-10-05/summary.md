# 수집 서비스 부팅 준비 상태 점검 — 2026-10-05

10/5 재부팅 뒤 DNS 오류와 수신 시각 역행의 원인을 운영 설정·journal·RTC로 대조했다.
**사용자 서비스의 네트워크 대기가 실제로 작동하지 않으며, 최초 NTP 동기화를 기다리는
조건도 없다.** 현재 하드웨어 시계는 NTP 동기화된 시스템 시각보다 약 41초 빠르다.
수집 코드·서비스·시각·NTP 설정은 변경하지 않았고 재부팅·재시작 시험도 하지 않았다.

[직전 호가 점검](../depth-status-followup-2026-10-05/summary.md)의 종료 7개·재생 결과는
그대로 유지한다. 이번은 부팅 원인 점검이며 24시간 관찰이나 새 패치 검증이 아니다.

## 실제 기동 순서

[호스트 조회](host.json)는 설치된 systemd `255.4-1ubuntu8.16`, 사용자·시스템 관리자의
의존 관계, 현재 시각 동기화와 유선 연결 상태를 기록한다. 같은 boot ID의 journal
`__MONOTONIC_TIMESTAMP`로 [순서와 간격](findings.json)을 계산했다. NTP 보정 전후의
벽시계 시각을 빼서 시간을 계산하지 않았다.

| 부팅 후 monotonic 초 | 사건 | depth 기동 이후 |
|---|---|---:|
| 8.552878 | depth 기동; aggTrade도 거의 같은 시각 | 0초 |
| 12.487774 | 시스템 NetworkManager-wait-online 완료 | 3.934896초 |
| 111.370242 | 최초 NTP 동기화 완료 | 102.817364초 |

당시 유선 링크·기본 경로·DNS 준비 전 이름 해석 오류와, 이후 시각 보정이 발생한 순서를
설명한다. 처음 NTP 응답 4회가 시간 초과였다는 로그도 있지만, 이번 한 번의 기록으로
그 원인을 공유기·방화벽·외부 서버 중 하나로 확정하지 않았다. 추가 네트워크 시험은 0회다.

## 네트워크 대기 설정이 효과 없는 이유

depth와 aggTrade는 `Wants=network-online.target`, `After=network-online.target`을
설정한 **사용자 서비스**다. 현재 조회에서 사용자 관리자의 이 target은 `LoadState=not-found`,
시스템 관리자의 같은 이름은 `loaded/active`였다. 시스템의 NetworkManager 대기 서비스는
이미 enabled였지만 사용자 수집 서비스가 그 완료를 기다리지는 않았다.

systemd의 사용자·시스템 유닛 검색 경로는 서로 구분되며, `Wants` 대상이 시작되지 않아도
본 서비스의 기동을 막지는 않는다. 이번 `not-found` 조회와 실제 선행 기동은 이 동작에
부합한다. [systemd v255 공식 유닛 설명](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.unit.xml)

NetworkManager의 wait-online은 초기 네트워크 설정 완료를 기다리는 기능이다. 지속적인
인터넷 연결이나 거래소 응답을 보장하는 신호는 아니다.
[NetworkManager 공식 설명](https://networkmanager.dev/docs/api/latest/NetworkManager-wait-online.service.html)

`/var/lib/systemd/linger/yoonhc` 파일이 있고 사용자 관리자의 부팅 기동도 확인했다.
진단 도구의 `loginctl --property=Linger,State,RuntimePath`는 빈 출력이어서 해당 결과를
linger의 근거로 쓰지 않았다. 설치된 두 수집 유닛은 저장소의 유닛과 동일하다.

## 시각 보정과 RTC

22:02 조회에서 `NTP=yes`, `NTPSynchronized=yes`, `systemd-timesyncd=active`였으며,
현재 사용 서버는 `ntp.ubuntu.com`이다. 반면 `systemd-time-wait-sync.service`는 disabled,
시스템 `time-sync.target`은 inactive, 사용자 쪽 같은 target은 not-found다. 두 수집기에는
초기 동기화 완료를 확인하는 별도 경로가 없다.

timesyncd의 기동과 `time-set.target` 도달은 최초 원격 동기화 완료와 다르다. 공식 문서도
정확한 초기 시각을 기다리는 별도 절차와 `/run/systemd/timesync/synchronized` 표시를
구분한다. 이 표시는 현재 부팅의 초기 동기화 근거로 쓸 수 있지만 이후 영구적인 시각
정확도를 보증하지는 않는다.
[systemd-timesyncd 공식 설명](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd-timesyncd.service.xml),
[time-wait-sync 공식 설명](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd-time-wait-sync.service.xml)

`LocalRTC=yes`, `/etc/adjtime`의 `LOCAL`이 확인됐고, timesyncd는 이 모드에서 시스템
시각을 RTC로 반영하지 않는다는 메시지를 실제로 남겼다. 설치 버전의
[공식 구현](https://raw.githubusercontent.com/systemd/systemd/v255/src/timesync/timesyncd.c)과 일치한다.
[RTC 읽기 3회](rtc-observation.json)는 설정된 Asia/Seoul로 해석할 때 시스템보다
40.84~41.04초 빨랐다. RTC 읽기는 1초 해상도이므로 소수점 수준의 정확도를 주장하지 않는다.
[커널 기록](kernel-rtc.json)도 부팅 시 RTC에서 시스템 시계를 초기화했음을 보여준다.

직전 점검의 NTP 보정 약 -41.041초, 실제 수신 시각 약 -40.939초 역행, 이번 RTC 오차가
서로 일관된다. **빠른 RTC로 시작한 뒤 첫 동기화 전에 수집했다는 설명에 근거가 있다.**
RTC가 처음 왜 빨라졌는지, 배터리·하드웨어 문제인지까지는 확인하지 않았다.

두 수집기의 세션·flush·회전 시간 제한은 `time.monotonic()`을 사용한다. 수신 시각과
상태 신선도는 벽시계에 의존하며, 두 status 함수 모두 음수 age를 healthy로 인정하지 않는다.
원문 수신 시각을 수정하거나 기존 품질 상태를 바꾸지 않았다.

## 권장 보완과 검증 범위

**사용자 서비스 구조를 유지하면서 두 스트림의 실행 직전에 네트워크·초기 시각 준비를
확인하는 작은 공통 절차를 추가하는 것**을 추천한다. 호가 계산·요청 예산의 변경은 필요하지 않다.
구체적인 조건·시간 제한·시험 범위는 [후보 범위](recommended-change.md)에 정리했다.
이 문서는 제안이며 해당 검사기나 유닛 변경을 구현·설치한 것은 아니다.

시각을 기다리면 이번 부팅에서는 첫 수집이 기존보다 약 103초 이상 늦어지는 비용이 있다.
그동안의 호가를 나중에 완전히 복구할 수 있다고 가정하지 않는다. 준비 실패를 무시하고
자동 통과시키는 방식은 제안하지 않는다.

RTC는 다른 OS와의 공유 조건을 확인한 뒤 UTC 기준으로 맞추는 것이 적절하다. 공식
`set-local-rtc 0`은 기본적으로 시스템 시각을 RTC에 반영한다. 현재 시스템 시각을 빠른 RTC로
되돌리는 옵션은 이번 보완에 필요하지 않다. 실제 명령은 실행하지 않았다.
[timedatectl 공식 설명](https://raw.githubusercontent.com/systemd/systemd/v255/man/timedatectl.xml)

운영 source·설정 파일의 해시는 점검 전후 동일하다. 사용자 버스 접근이 샌드박스에서
차단돼 읽기 전용 호스트 조회를 승인받았다. 커널 조회의 첫 `--kernel` 옵션 오류는
`-k`로 수정해 조회했고 최초 실패 기록도 보존했다. 문서·근거·도구 구문 검사는
[검증 기록](validation.json)에 남긴다. 운영 변경이나 전체 회귀 시험은 수행하지 않았다.

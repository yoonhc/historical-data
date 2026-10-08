# 수집 호스트의 네트워크 관측

`marketdata-network-observer.timer`는 매분 UTC 20초에 별도 Python 프로세스를 실행한다.
활성화 5초 뒤에도 실행하며, 수집기·카탈로그·네트워크 설정을 변경하지 않는다. 목적은 다음
통신 장애 때 유선 연결, 이름 해석, 외부 접속의 실패를 같은 시간대 수집 로그와 대조하는 것이다.

| 관측 항목 | 기록과 한계 |
|---|---|
| 선택 경로·링크 | 외부 시험 IP로 향하는 OS 경로, 인터페이스, gateway, carrier, operstate. 바이낸스 경로와 같다는 보장은 없음 |
| gateway ICMP | 현재 선택된 gateway에 ping 1회. ICMP 무응답만으로 공유기 장애를 확정하지 않음 |
| 외부 숫자 IP TCP | 설정된 IP의 443 포트로 DNS 없이 연결. 해당 목적지의 도달성만 확인 |
| 이름 해석 | `getent ahosts`로 REST·스트림 호스트의 OS/NSS 해석 결과와 시간을 기록. 캐시 응답일 수 있음 |
| REST HTTPS | 공개 `/fapi/v1/ping`의 HTTP 상태, DNS·TCP·TLS·전체 시간, 접속 IP |
| 스트림 호스트 HTTPS | `fstream.binance.com`에 HEAD 요청. TLS·HTTP 도달성이며 WebSocket 구독·데이터 수신 검사는 아님 |

probe는 독립적으로 실행하고 개별 시작·완료 시각을 남긴다. curl 설정 파일과 proxy는 사용하지
않는 직접 접속이다. 수집기가 별도 proxy를 쓰는 환경으로 바뀌면 같은 통신 경로의 검사로
해석하면 안 된다. REST HTTP 429와 DNS 해석 실패도 서로 구분해 기록한다.
REST 공개 경로는 [Binance 공식 문서](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data),
curl 시간과 HEAD 의미는 [공식 curl 설명](https://curl.se/docs/manpage.html#-w)을 참고한다.

각 외부 요청은 최대 2~5초, 전체 서비스는 20초로 제한한다. 실패한 probe가 있어도 다른
관측 결과를 보존하고 `status=degraded`와 `failed_probes`를 남긴다. 서비스 종료 코드 0은
**관측 기록 작성 성공**이다. 네트워크 정상 여부는 JSON을 함께 확인한다. 출력 실패·프로필
오류는 서비스 실패다. lock으로 중복 실행을 막으며 DNS 변경·공유기 조작·서비스 재시작·외부
알림은 하지 않는다.

설정은 호스트별로 분리한다.

- `~/.config/marketdata/network-observer.env`: Python·스크립트·프로필의 절대 경로.
- `~/.config/marketdata/network-observer.toml`: 출력 경로, 예상 인터페이스, 외부 시험 IP.
- [프로필 예시](../configs/network-observer.example.toml)의 `expected_interface=""`는 OS가 선택한
  인터페이스를 허용한다. 값을 지정하면 불일치를 기록할 뿐 라우팅을 강제하지 않는다.
- 현재 설치는 `/usr/bin/python3`의 표준 라이브러리만 사용하고 `ip`, `ping`, `getent`, `curl`이
  필요하다. 실행 사용자는 `yoonhc`이며 linger가 활성화되어 있다. VM 이전 시 새 경로·인터페이스·
  접속 가능한 시험 IP와 실행 권한을 확인하고 새 호스트에서 실제 서비스 실행을 검증한다.

현재 출력은 `reports/network-observer/<UTC 날짜>.jsonl`에 계속 추가하고 `latest.json`을
원자적으로 교체한다. 과거 로그를 자동 삭제하지 않는다. 한 번의 정상 표본이나 1분 간격의
표본 사이에는 짧은 장애를 놓칠 수 있다. 타이머의 Persistent 설정은 부재 중 관측값을 복원하지
않으며 돌아왔을 때 한 번 실행하는 기능이다. 관측 시각·기록 간격·수집기 journal을 함께 본다.

```bash
systemctl --user list-timers marketdata-network-observer.timer
systemctl --user show marketdata-network-observer.service \
  --property=Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp
journalctl --user -u marketdata-network-observer.service -n 20 --no-pager
python3 -m json.tool reports/network-observer/latest.json

# 이 관측의 새 예약만 중지. 실행 중인 짧은 관측과 기존 수집·백업은 그대로 둔다.
systemctl --user disable --now marketdata-network-observer.timer
```

관측 로그는 진단 자료이며 시장 데이터의 정규 저장소·백업 범위에 자동 추가하지 않는다.
설치 점검과 첫 실제 서비스 결과는
[호가 보강·네트워크 관측 기록](../reports/baseline/depth-refresh-2026-10-01/summary.md)에 남긴다.

10/5 재부팅 뒤의 [기동 순서·시각 점검](../reports/baseline/stream-startup-readiness-2026-10-05/summary.md)에서는
사용자 수집 서비스가 시스템의 network-online 완료를 기다리지 못하고, 최초 NTP 동기화보다
약 103초 먼저 시작했음을 확인했다. RTC도 시스템 시각보다 약 41초 빨랐다. 당시 사용자 유닛의
네트워크 대기 표기를 실제 준비 완료 보장으로 해석하지 않는다. 그 점검 자체는 읽기 전용이었다.
이후 [10/9 실제 부팅 확인](../reports/baseline/system-cutover-2026-10-08/post-reboot-summary.md)까지
시스템 수집·Chrony·UTC RTC 전환을 완료했다. 현재 운영은 [시스템 서비스 안내](system-runtime-operations.md)를 따른다.
초기 준비 통과와 실행 중 네트워크의 지속적인 정상 여부는 별개다.

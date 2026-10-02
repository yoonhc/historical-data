# 사용자 systemd 자동 수집

이 디렉터리의 서비스·타이머는 현재 머신의 사용자 systemd에 연결되어 있습니다.
다른 위치에서 사용하려면 `.service` 파일의 `WorkingDirectory`와 `ExecStart`를
실제 프로젝트 경로로 수정하세요. `.venv/bin/marketdata`가 해당 위치에 설치되어 있어야 합니다.

- `marketdata-daily.timer`: 매일 UTC 03:20 이후 최대 5분의 분산 지연을 두고 일별 자료를 갱신합니다.
- `marketdata-oi.timer`: 매 5분 경계에서 45초 뒤에 최근 REST OI를 수집합니다.
- `marketdata-oi-probe.timer`: 15초마다 원본 HTTP 응답을 관측합니다. canonical 게시 없이
  수신 이력만 추가하며, `AccuracySec=1s`와 `Persistent=false`를 사용합니다.
- `marketdata-oi-quality.timer`: UTC 03:40 / 한국 시각 12:40에 최근 7일의 OI 품질 보고서를 만듭니다.
  [관측 정책·보고서·종료 코드](../../docs/oi-quality.md)를 함께 확인하세요.
- 기존 daily/oi 타이머는 활성화 후 30초부터 첫 수집을 실행합니다. 일별 작업에는 최대 5분의
  분산 지연이 추가됩니다. 관측기는 2초, 품질 보고서는 5초 후 첫 실행을 예약합니다.
- 기존 네 타이머는 enabled/active이며 실제 자동 실행 성공을 확인했습니다. `yoonhc`의 linger도 활성화했습니다.
- 타이머가 실행하는 작업은 `Type=oneshot`이므로 성공한 서비스가 `inactive (dead)`인 것은 정상입니다. 타이머의 active 여부와 서비스의 `Result`를 확인하세요.
- 실패 시 일별 작업은 60초, OI는 30초 후 재시도합니다. 일별 작업은 10분간 5회, OI는 5분간 4회로 시작 횟수를 제한합니다.
- 최신 완료 보고서는 `state/daily-latest.json`, `state/oi-latest.json`입니다. 실행 이력은 SQLite에도 남습니다.
- 정기 수집기는 쓰기 잠금을 최대 60초 기다립니다. 장기 `backfill-set`은 다운로드·정규화를 잠금 밖에서 수행하고 하루 단위 게시 사이에 양보합니다.

추가한 상시 수집·유지보수 구성은 다음과 같습니다.

- `marketdata-aggtrade.service`, `marketdata-depth.service`: 1시간 세션 후 15초 뒤 재시작.
  `Type=simple`, `Restart=always`이며 `SIGINT`로 안전한 종료를 요청합니다.
- `marketdata-pack.timer`: 매시 UTC 15분 및 활성화 5분 후 종료된 이벤트 파일을 묶고 검증된 개별 사본을 정리합니다.
- `marketdata-backup.timer`: UTC 04:10 / 한국 시각 13:10에 전체 검증 후 당일 백업을 생성합니다.
  현재 두 번째 SSD의 `/mnt/backup/marketdata-historical/automatic`으로 저장하며
  `--incremental`로 이전 날짜 사본의 동일 파일을 재사용합니다. 첫 사본은 전체 복사합니다.
  `--backup-mount /mnt/backup`으로 마운트가 없는 경우 로컬 경로에 대신 쓰지 않게 합니다.
  다른 머신으로 옮기면 `ExecStart`의 백업 경로·마운트도 변경하고 전용 폴더를 미리 만드세요.
  명시한 목적지가 없으면 실패합니다. 서비스 수정 후 `systemctl --user daemon-reload`가 필요합니다.
- 두 유지보수는 `--wait-for-lock`으로 전용 잠금을 같은 프로세스에서 기다린 뒤 실행합니다.
  정상적인 잠금 대기는 재시도 횟수를 소모하지 않으며, journal에
  `waiting_for_maintenance_lock`을 남깁니다. 대기 중에는 기존 최신 보고서를 유지합니다.
  대기와 작업의 합계는 서비스의 6시간 제한을 적용하고, `SIGINT`로 중단할 수 있습니다.
  실제 실패는 120초 후 재시도합니다(1시간 최대 4회).
  원문·표준 데이터와 백업은 날짜에 따른 자동 삭제 없이 보존합니다.
- [세션 간 공백·재접속 제한·백업 경로·상태 확인](../../docs/stream-services.md)을 확인하세요.
- `marketdata-network-observer.timer`: 매분 20초 및 활성화 5초 뒤에 독립 네트워크 관측을
  실행합니다. 호스트별 Python·스크립트·프로필은 `~/.config/marketdata/network-observer.env`에,
  출력 경로·인터페이스·외부 시험 IP는 별도 TOML에 둡니다. 기존 유닛을 수정하지 않습니다.
  [관측 범위·이식·새 예약만 중지](../../docs/network-observer.md)를 참고하세요.

```bash
systemctl --user list-timers --all 'marketdata-*'
systemctl --user show marketdata-daily.service marketdata-oi.service \
  --property=Result --property=ExecMainStatus --property=ExecMainExitTimestamp
journalctl --user -u marketdata-oi.service -n 30 --no-pager
loginctl show-user "$USER" --property=Linger

# 수동 재실행. 진행 중인 동일 서비스는 새로 중복 실행하지 않습니다.
systemctl --user start marketdata-oi.service

# 반복 실패 원인을 해결한 뒤 시작 제한을 해제하고 재실행
systemctl --user reset-failed marketdata-oi.service
systemctl --user start marketdata-oi.service

# 자동 실행 중지. 이미 실행 중인 작업까지 멈추려면 서비스도 stop합니다.
systemctl --user disable --now marketdata-daily.timer marketdata-oi.timer \
  marketdata-oi-probe.timer marketdata-oi-quality.timer
systemctl --user stop marketdata-daily.service marketdata-oi.service \
  marketdata-oi-probe.service marketdata-oi-quality.service
```

daily/oi/oi-quality의 `Persistent=true`로 머신을 꺼 둔 동안 놓친 실행을 재개하고, 수집기는 저장된 운영 범위에서
누락을 다시 계산합니다. 이미 보존한 원본·revision·스냅샷은 유지합니다. 머신이 꺼져 있거나
절전 중에는 수집할 수 없으며, REST 제공 기간을 넘긴 누락은 `status`에 남습니다.
oi-probe는 과거 시점의 HTTP 응답을 복구할 수 없으므로 `Persistent=false`로 실행하고,
관측 공백을 품질 보고서에 표시합니다.

설치 및 상태 확인 명령은 [프로젝트 README](../../README.md#갱신과-운영)에 있습니다.

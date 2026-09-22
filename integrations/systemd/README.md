# 사용자 systemd 자동 수집

이 디렉터리의 서비스·타이머는 현재 머신의 사용자 systemd에 연결되어 있습니다.
다른 위치에서 사용하려면 두 `.service` 파일의 `WorkingDirectory`와 `ExecStart`를
실제 프로젝트 경로로 수정하세요. `.venv/bin/marketdata`가 해당 위치에 설치되어 있어야 합니다.

- `marketdata-daily.timer`: 매일 UTC 03:20 이후 최대 5분의 분산 지연을 두고 일별 자료를 갱신합니다.
- `marketdata-oi.timer`: 매 5분 경계에서 45초 뒤에 최근 REST OI를 수집합니다.
- 활성화 후 30초부터 첫 수집을 실행합니다. 일별 작업에는 최대 5분의 분산 지연이 추가됩니다.
- 두 타이머는 enabled/active이며 실제 자동 실행 성공을 확인했습니다. `yoonhc`의 linger도 활성화했습니다.
- 작업은 `Type=oneshot`이므로 성공한 서비스가 `inactive (dead)`인 것은 정상입니다. 타이머의 active 여부와 서비스의 `Result`를 확인하세요.
- 실패 시 일별 작업은 60초, OI는 30초 후 재시도합니다. 일별 작업은 10분간 5회, OI는 5분간 4회로 시작 횟수를 제한합니다.
- 최신 완료 보고서는 `state/daily-latest.json`, `state/oi-latest.json`입니다. 실행 이력은 SQLite에도 남습니다.
- 정기 수집기는 쓰기 잠금을 최대 60초 기다립니다. 장기 `backfill-set`은 배치마다 잠금을 반납해 함께 실행할 수 있습니다.

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
systemctl --user disable --now marketdata-daily.timer marketdata-oi.timer
systemctl --user stop marketdata-daily.service marketdata-oi.service
```

`Persistent=true`로 머신을 꺼 둔 동안 놓친 실행을 재개하고, 수집기는 저장된 운영 범위에서
누락을 다시 계산합니다. 이미 보존한 원본·revision·스냅샷은 유지합니다. 머신이 꺼져 있거나
절전 중에는 수집할 수 없으며, REST 제공 기간을 넘긴 누락은 `status`에 남습니다.

설치 및 상태 확인 명령은 [프로젝트 README](../../README.md#갱신과-운영)에 있습니다.

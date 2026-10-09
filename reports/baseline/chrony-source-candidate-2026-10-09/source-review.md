# 공개 시간원 실측과 선정 한계

**시간원 네 개를 바로 운영에 넣는 교체안은 확정하지 않았다.**
Cloudflare와 국내 Pool 일부 주소는 30분 관측에서 꾸준히 응답했지만,
여러 해외 시간원은 수신 횟수가 적었고 국내 Pool 서버의 실제 운영 주체는 확인하지 못했다.
또 정상 응답하는 서버가 있는 구성에서도 reference 갱신이 600초를 넘었다.

검토용 `candidate-review-only.conf`는 첫 번째 실제 시험 조합을 그대로 고정했다.
이 구성의 알려진 한계를 숨기고 운영 적용 대상으로 추천하지 않는다.
[구조화된 선정 결과](source-review.json)의 `assessment=NOT_QUALIFIED_FOR_OPERATING_DEPLOYMENT`도 유지했다.

## 측정 방식

이 호스트에서 별도 Chrony 4.5 프로세스 세 개를 차례로 실행했다. 각 묶음은 30분이며 겹쳐 운영하지 않았다.
운영 Chrony는 그대로 두고 시험 daemon만 `-x -U`, 일반 계정, CapEff 0, 별도 Unix socket으로 실행했다.
실제 설치 바이너리와 SHA-256이 같은 복사본을 사용했다.
운영 시간원·시각 제어·RTC를 바꾸지 않았다.

각 묶음의 네 주소는 조회 당시 공식 hostname 또는 Pool DNS 응답에 있는 주소를 고정했다.
30초마다 tracking/sources/sourcestats/selectdata/ntpdata를 저장했다.
설정은 `iburst minpoll 6 maxpoll 8`이며 `prefer`, 강제 재선택, 강제 추가 burst가 없다.
아래 RX는 daemon이 관측한 응답 수다. TX-RX를 특정 서버나 ISP에서 소실된 패킷 수로 단정하지 않는다.

시각 품질은 실제 tracking 응답에 기존 `assess_clock()`을 적용한 결과다.
`-x` daemon의 계산을 호스트 UTC 동기화 성공이나 전체 운영 gate 통과로 확대하지 않는다.
source offset도 시험 daemon의 추정 시계와 비교한 값이며, 각 서버의 마지막 측정 시각이 서로 다를 수 있다.
서버 간 동시 UTC 오차를 직접 측정한 값이 아니다.

측정 결과 표와 선택·reference·준비 판정 이력은 [집계 JSON](observation-analysis.json)에 있다.
집계 코드는 [analyze_observations.py](analyze_observations.py)이며 원본 JSONL을 수정하지 않는다.

| 묶음 | 운영 주체 또는 분류 | 실제 주소 | origin ASN | RX / TX = good RX / TX | 마지막 peer delay |
|---|---|---|---|---|---|
| 1 | cloudflare | `162.159.200.1` | 13335 | 30 / 30 | 3.39ms |
| 1 | nict | `133.243.238.163` | 9355 | 6 / 21 | 42.14ms |
| 1 | netnod | `194.58.200.20` | 57021 | 5 / 19 | 244.46ms |
| 1 | ptb | `192.53.103.108` | 680 | 3 / 17 | 273.83ms |
| 2 | nict | `61.205.120.130` | 17511 | 5 / 19 | 158.53ms |
| 2 | internet_multifeed | `210.173.160.27` | 7521 | 1 / 16 | 41.88ms |
| 2 | korea_pool_operator_unconfirmed | `115.15.44.33` | 4766 | 24 / 24 | 10.68ms |
| 2 | nist | `132.163.96.1` | 49 | 5 / 19 | 177.73ms |
| 3 | sidn | `94.198.159.10` | 1140 | 3 / 16 | 246.99ms |
| 3 | netnod | `194.58.203.20` | 57021 | 4 / 18 | 286.77ms |
| 3 | ptb | `192.53.103.104` | 680 | 6 / 18 | 269.76ms |
| 3 | korea_pool_operator_unconfirmed | `121.174.142.81` | 4766 | 24 / 24 | 9.21ms |

## 응답과 reference 갱신의 구분

첫 묶음은 10:36:12–11:06:12 KST다. 준비 조건 최초 통과 후 거부 표본은 없었지만,
reference age는 573.219초까지 늘었다. 처음 Cloudflare를 선택했다가 10:49:42 관측부터 `*`가 없어졌고,
10:58:12 관측에서 NICT가 `*`로 나타나며 reference가 바뀌었다.
따라서 Cloudflare가 30/30 응답했다는 사실을 이 묶음 전체의 reference가 항상 갱신됐다는 뜻으로 쓰지 않는다.

두 번째 묶음은 11:08:18–11:38:18 KST다. 국내 `115.15.44.33`은 24/24 응답했지만,
11:22:48 관측부터 `*`가 사라졌고 11:31:48부터 reference age 초과로 준비 판정이 거부됐다.
마지막 age는 1,018.612초였다. 마지막 `selectdata`에는 여러 후보가 `U` 상태로 기록됐다.
Chrony의 `U`는 다른 최적 시간원 선택 후 새 측정을 기다리는 상태다.
응답 가용성, 내부 선택 진행, 실제 reference 갱신을 같은 지표로 취급하지 않는다.
[Chrony 4.5 selectdata](https://chrony-project.org/doc/4.5/chronyc.html#selectdata).

완전 무응답·간헐 응답 시 600초 초과는 [격리 장애 시험](fault-validation.md)에 따로 기록했다.
공개 시간원 실측과 합성 장애 시험을 섞어 실제 과거 장애를 재현했다고 주장하지 않는다.

세 번째 묶음은 12:05:17–12:35:17 KST다. 국내 `121.174.142.81`은 24/24 응답했고,
준비 조건 최초 통과 이후 거부 표본은 없었다. 최대 reference age는 175.714초였다.
그러나 나머지 세 서버는 3/16·4/18·6/18 응답에 그쳤으므로 이 묶음 전체를 검증된 네 시간원으로 채택하지 않는다.

## 운영 주체와 윤초 정책

| 서비스 | 확인된 운영 주체와 시간 기준 | 윤초 정책 확인 범위 |
|---|---|---|
| Cloudflare | 공식 공개 anycast NTP 서비스 | 공식 문서에 non-smear가 명시됨 |
| NICT | 일본 정보통신연구기구의 공개 NTP | 공식 2017 윤초 안내에 LI 비트·UTC 방식 설명. 현재 응답은 Normal. 다음 윤초의 전체 동작은 이번 실측으로 인증하지 않음 |
| Netnod | Netnod와 스웨덴 국가 시각 기관의 시간 서비스 | 공식 2015 안내는 윤초 순간 잠시 서비스 중단 방식. 현재 UTC 서비스와 구분하며 2015 동작을 현재 정책 보장으로 쓰지 않음 |
| PTB | 독일 국가표준기관의 UTC(PTB) 서비스 | UTC 제공은 확인. 확인한 페이지에서 현재 smear 여부를 별도로 명시한 계약까지 확보하지 못함 |
| INTERNET MULTIFEED | MFEED 운영, NICT로부터 기준 시각 수신 | NICT 기반이라는 사실만으로 독립된 기준 시계나 현재 세부 윤초 동작까지 단정하지 않음 |
| NIST | 미국 국가표준기관의 공식 ITS | UTC(NIST) 제공은 확인. 이번 FAQ만으로 특정 endpoint의 현재 smear 정책까지 인증하지 않음 |
| TimeNL | SIDN Labs 운영, 여러 협력 기관·참조 시계 | 운영 주체·참조 시계는 확인. 읽은 공식 페이지에서 non-smear 명시 문구는 찾지 못했으므로 명시 확인으로 표시하지 않음 |
| 한국 NTP Pool 두 주소 | 해당 날짜 Pool DNS에서 확인 | Pool의 일반적인 non-smear 설명과 개별 서버 운영 주체·현재 설정을 구분. 두 서버의 운영 주체는 미확인 |

자료: [Cloudflare](https://developers.cloudflare.com/time-services/ntp/),
[NICT 공개 서비스](https://www.nict.go.jp/sts/ntp.html)와
[NICT 윤초 안내](https://www2.nict.go.jp/sts/jst/QandA/reference/leapsec-addendum2017.html),
[Netnod 서비스](https://www.netnod.se/ntp/connect-to-ntp-servers)와
[Netnod 2015 윤초 안내](https://www.netnod.se/news/leap-second-added-to-the-global-time-scale),
[PTB 기술 안내](https://www.ptb.de/cms/ptb/fachabteilungen/abt9/gruppe-95/ref-952/zeitsynchronisation-technische-hinweise.html),
[MFEED 서비스](https://www.mfeed.ad.jp/ntp/detail/)와 [구성](https://www.mfeed.ad.jp/ntp/network/),
[NIST ITS](https://www.nist.gov/pml/time-and-frequency-division/time-distribution/internet-time-service-its),
[TimeNL](https://time.nl/index_en.html), [한국 Pool](https://www.ntppool.org/en/zone/kr).

모든 마지막 응답의 LI가 Normal인 것은 윤초 시점의 smear 유무를 증명하지 않는다.
Google처럼 smear를 명시한 시간원은 이번 조합에서 제외했다.
기존 로컬 `leapsecmode slew`는 서버의 leap smear와 구분하며 변경하지 않았다.
[Google 정책](https://developers.google.com/time/faq), [Chrony 설정](https://chrony-project.org/doc/4.5/chrony.conf.html).
이처럼 현재 정책이 미확인인 주소까지 “non-smear 네 개 검증 완료”로 묶지 않는다.

## 주소와 독립성

[RIPEstat 조회 원문](public-address-networks.json)은 DNS로 확인한 실제 주소의 BGP origin ASN과 prefix를 담는다.
첫 묶음의 ASN은 13335·9355·57021·680으로 서로 달랐다.
다른 운영 주체와 ASN을 확인한 것이며, 이 집에서 서버까지의 실제 통신 경로가 서로 독립적이라는 증거는 아니다.
단일 호스트·공유기·인터넷 회선은 여전히 공통이고, 해외 transit나 기준 시계의 공유도 완전히 확인하지 않았다.

국내 Pool 두 주소는 모두 ASN 4766이었다. 네트워크 사업자를 NTP 서버 운영자로 대신 기재하지 않는다.
NICT 두 주소는 서로 다른 ASN에서도 응답했지만 서비스 운영 주체는 같다.
MFEED와 NICT는 운영 주체가 달라도 기준 시각이 연결되어 있다.
Netnod의 두 주소와 PTB의 두 주소도 각각 별도 독립 운영자로 세지 않는다.
서로 다른 실제 시간원을 확보하라는 [RFC 8633 권고](https://www.rfc-editor.org/rfc/rfc8633.html#section-3.2)는
네 개의 DNS 이름 또는 네 개의 IP만으로 충족됐다고 주장할 수 없다.

검토 설정은 IPv4 네 개를 고정하고 pool/include/sourcedir를 추가하지 않는다.
따라서 승인된 이 파일의 DNS 재해석으로 `91.189.91.157`이 다시 편입되지는 않는다.
그러나 고정 주소의 수명·소유자 변경, anycast 뒤 실제 서버 변경, 비인증 NTP의 한계는 남는다.
주소가 바뀌면 다시 조사하고 새 계획·승인을 연결해야 한다.

TimeNL은 이름 사용을 권하며 주소 변경 가능성을 명시한다. 고정 IP를 영구적인 계약으로 간주하지 않는다.
Pool을 향후 다시 사용한다면 재해석 뒤 실주소·운영 주체·윤초 정책을 재평가해야 한다.
원래 설정으로 rollback하면 원래 Ubuntu pool의 DNS 동작도 돌아오므로 문제 IP 재유입 가능성이 있다.

## 남은 판단

이번 조사는 불안정한 원격 시간원으로 무조건 교체하는 것보다 후보를 보류할 근거를 제공했다.
짧은 관측에서 정상인 주소는 장기 안정성이 입증된 주소가 아니며, 응답이 적었다는 사실만으로 서버의 영구 장애를 뜻하지도 않는다.
이번 설정 변경에 강제 선택·허용 나이 변경·Chrony 버전 교체·T11 업무 정책 변경을 끼워 넣지 않았다.

최초 public 시험은 sandbox DNS 제한, 다음 시도는 설치 경로에 붙은 AppArmor의 임시 설정 읽기 제한으로 종료됐다.
운영 AppArmor를 바꾸지 않고, 시각 제어 능력이 없는 일반 계정의 동일 바이너리 복사본으로 제한된 관측을 완료했다.
최종 시스템 서비스의 AppArmor·root 설치 권한을 이번 별도 daemon 관측으로 검증했다고 표현하지 않는다.

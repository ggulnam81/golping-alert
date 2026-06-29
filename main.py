"""
골핑 마켓 신규 상품 알림 자동화 스크립트
- 목표: https://market.golping.com/category/100011?filterYn=Y 의 최신 상품 감지
- 신규 상품 등록 시 이메일 발송
- 한국 시간(KST) 오전 11시 ~ 오후 8시(20시) 사이에만 동작

[테스트 방법]
  GitHub Actions 워크플로우 "🔍 신규 상품 확인 실행" Step 환경변수에
  FORCE_EMAIL=true 를 추가하면 변경 여부/시간 무관하게 무조건 이메일 발송
"""

import os
import sys
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

# ────────────────────────────────────────────────
# ❶ 테스트 모드 확인 (FORCE_EMAIL=true 이면 모든 조건 무시하고 이메일 강제 발송)
# ────────────────────────────────────────────────
FORCE_EMAIL = os.environ.get("FORCE_EMAIL", "false").lower() == "true"

if FORCE_EMAIL:
    print("[TEST] ⚡ FORCE_EMAIL=true — 테스트 모드 활성화 (시간·중복 체크 생략)")

# ────────────────────────────────────────────────
# ❷ 시간 방어 로직 — KST 11:00 ~ 20:00 사이가 아니면 종료
# ────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
current_hour = now_kst.hour

print(f"[INFO] 현재 KST 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}")

if not FORCE_EMAIL and not (11 <= current_hour < 20):
    print(f"[INFO] 운영 시간(11시~20시) 외({current_hour}시)이므로 종료합니다.")
    print("[INFO] 이메일을 받으려면 KST 11~20시 사이에 실행하거나 FORCE_EMAIL=true 를 사용하세요.")
    sys.exit(0)

# ────────────────────────────────────────────────
# ❸ 설정값 (환경 변수 → GitHub Secrets)
# ────────────────────────────────────────────────
SENDER_EMAIL    = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECEIVER_EMAIL  = os.environ.get("RECEIVER_EMAIL")

TARGET_URL = "https://market.golping.com/category/100011?filterYn=Y"
STATE_FILE = "last_product.txt"

print(f"[INFO] SENDER_EMAIL  설정 여부: {'✅' if SENDER_EMAIL else '❌ 미설정'}")
print(f"[INFO] SENDER_PASSWORD 설정 여부: {'✅' if SENDER_PASSWORD else '❌ 미설정'}")
print(f"[INFO] RECEIVER_EMAIL 설정 여부: {'✅' if RECEIVER_EMAIL else '❌ 미설정'}")

# ────────────────────────────────────────────────
# ❹ 웹 스크래핑 — requests + BeautifulSoup
# ────────────────────────────────────────────────
def fetch_top_product() -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://market.golping.com/",
    }

    print(f"[INFO] 페이지 요청 중: {TARGET_URL}")
    try:
        response = requests.get(TARGET_URL, headers=headers, timeout=20)
        response.raise_for_status()
        print(f"[INFO] HTTP 응답 코드: {response.status_code}")
        print(f"[INFO] 응답 HTML 크기: {len(response.text)} 자")
    except requests.RequestException as e:
        print(f"[ERROR] 페이지 요청 실패: {e}")
        return None

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[ERROR] beautifulsoup4 미설치")
        sys.exit(1)

    soup = BeautifulSoup(response.text, "lxml")

    # 골핑 마켓 CSS 셀렉터 (우선순위 순)
    selectors = [
        ".product-list li:first-child .product-name",
        ".product-list li:first-child .name",
        ".goods-list li:first-child .goods-name",
        ".item-list li:first-child .item-name",
        "[class*='productList'] [class*='name']:first-of-type",
        "[class*='goodsList'] [class*='name']:first-of-type",
        "[class*='itemList'] [class*='name']:first-of-type",
        ".product-item:first-child .title",
        ".goods-item:first-child .title",
        "li.product:first-child span.name",
    ]

    for selector in selectors:
        try:
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                name = el.get_text(strip=True)
                print(f"[INFO] ✅ 상품명 추출 성공 (셀렉터: {selector}): {name}")
                return name
        except Exception:
            continue

    # 폴백: 상품 URL 패턴을 포함한 링크에서 텍스트 추출
    print("[INFO] CSS 셀렉터 실패 → 링크 폴백 시도 중...")
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if any(k in href for k in ["/product/", "/goods/", "/item/"]) and len(text) > 3:
            print(f"[INFO] ✅ 상품명 추출 성공 (링크 폴백): {text}")
            return text

    # 디버그: 실제 HTML 일부 출력 (구조 파악용)
    print("[WARN] ❌ 상품명 추출 실패 — 아래 HTML을 확인하세요:")
    print(f"[DEBUG] HTML 앞 3000자:\n{response.text[:3000]}")
    print("[WARN] 이 사이트는 JavaScript 동적 렌더링을 사용할 가능성이 높습니다.")
    return None


# ────────────────────────────────────────────────
# ❺ 이전 상품명 로드 / 저장
# ────────────────────────────────────────────────
def load_last_product() -> str:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            val = f.read().strip()
        print(f"[INFO] 저장된 이전 상품명: '{val}'")
        return val
    print("[INFO] last_product.txt 없음 (첫 실행)")
    return ""


def save_last_product(product_name: str) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(product_name)
    print(f"[INFO] 상품명 저장 완료: {product_name}")


# ────────────────────────────────────────────────
# ❻ 이메일 발송
# ────────────────────────────────────────────────
def send_email(product_name: str) -> None:
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL]):
        print("[ERROR] ❌ 이메일 환경변수 미설정 — GitHub Secrets를 확인하세요.")
        print(f"  SENDER_EMAIL: {'설정됨' if SENDER_EMAIL else '❌ 없음'}")
        print(f"  SENDER_PASSWORD: {'설정됨' if SENDER_PASSWORD else '❌ 없음'}")
        print(f"  RECEIVER_EMAIL: {'설정됨' if RECEIVER_EMAIL else '❌ 없음'}")
        sys.exit(1)

    subject = "[골핑 알림] 새로운 상품이 업데이트 되었습니다."
    body = (
        f"안녕하세요! 골핑 마켓 신상품 알림입니다.\n\n"
        f"업데이트 상품이 있습니다. 확인해 보세요!\n\n"
        f"📦 신규 상품명: {product_name}\n\n"
        f"🔗 바로가기: {TARGET_URL}\n\n"
        f"─────────────────────────────\n"
        f"알림 발송 시각 (KST): {now_kst.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"이 메일은 GitHub Actions에 의해 자동 발송되었습니다."
    )

    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    print(f"[INFO] Gmail SMTP 연결 시도 중 ({SENDER_EMAIL} → {RECEIVER_EMAIL})")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"[INFO] ✅ 이메일 발송 성공! → {RECEIVER_EMAIL}")
    except smtplib.SMTPAuthenticationError:
        print("[ERROR] ❌ Gmail 인증 실패!")
        print("[ERROR] 해결법: Gmail 앱 비밀번호(16자리)가 올바른지 확인하세요.")
        print("[ERROR] 일반 Gmail 비밀번호가 아닌 '앱 비밀번호'를 사용해야 합니다.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] ❌ 이메일 발송 실패: {e}")
        sys.exit(1)


# ────────────────────────────────────────────────
# ❼ 메인 실행
# ────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("골핑 마켓 신규 상품 감지 시작")
    print("=" * 55)

    # 1. 스크래핑
    current_product = fetch_top_product()

    if current_product is None:
        print("[ERROR] 상품명 추출 실패. 종료합니다.")
        print("[TIP] 위의 HTML 출력을 확인해 CSS 셀렉터를 수정하거나 Selenium으로 전환하세요.")
        sys.exit(1)

    # 2. 이전 상품명 로드
    last_product = load_last_product()
    print(f"[INFO] 현재 상품명: '{current_product}'")

    # 3. 변경 여부 확인 (FORCE_EMAIL=true면 무조건 통과)
    if not FORCE_EMAIL and current_product == last_product:
        print("[INFO] 신규 상품 없음 — 변경사항이 없어 종료합니다.")
        sys.exit(0)

    if FORCE_EMAIL:
        print("[TEST] FORCE_EMAIL 모드 — 변경 여부 무시하고 이메일 발송합니다.")

    # 4. 신규 상품 감지 → 저장 + 이메일
    print(f"[INFO] 🎉 신규 상품 감지: '{current_product}'")
    save_last_product(current_product)
    send_email(current_product)
    print("[INFO] 모든 작업 완료.")


if __name__ == "__main__":
    main()

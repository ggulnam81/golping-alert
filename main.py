"""
골핑 마켓 신규 상품 알림 자동화 스크립트
- 목표: https://market.golping.com/category/100011?filterYn=Y 의 최신 상품 감지
- 신규 상품 등록 시 이메일 발송
- 한국 시간(KST) 오전 11시 ~ 오후 8시(20시) 사이에만 동작
"""

import os
import sys
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

# ────────────────────────────────────────────────
# ❶ 시간 방어 로직 (이중 체크) — KST 11:00 ~ 20:00 사이가 아니면 즉시 종료
# ────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
current_hour = now_kst.hour  # 0 ~ 23

print(f"[INFO] 현재 KST 시각: {now_kst.strftime('%Y-%m-%d %H:%M:%S')}")

if not (11 <= current_hour < 20):
    print(f"[INFO] 운영 시간(11시~20시) 외({current_hour}시)이므로 스크립트를 종료합니다.")
    sys.exit(0)

# ────────────────────────────────────────────────
# ❷ 설정값 (환경 변수에서 불러오기 — GitHub Secrets 연동)
# ────────────────────────────────────────────────
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")       # 발신자 Gmail 주소
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") # Gmail 앱 비밀번호 (16자리)
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")   # 수신자 이메일 주소 (발신자와 동일해도 됨)

TARGET_URL = "https://market.golping.com/category/100011?filterYn=Y"
STATE_FILE = "last_product.txt"

# ────────────────────────────────────────────────
# ❸ 웹 스크래핑 — requests + BeautifulSoup
# ────────────────────────────────────────────────
def fetch_top_product() -> str | None:
    """
    골핑 마켓 카테고리 페이지에서 최상단(1번) 상품명을 추출합니다.

    ※ 만약 이 함수가 None을 반환하면, 해당 사이트가 JavaScript(동적 렌더링)로
       상품 목록을 불러오는 것입니다. 이 경우 아래 fetch_top_product_selenium()을
       대신 사용하세요 (주석 처리된 함수 참고).
    """
    headers = {
        # 실제 브라우저처럼 요청 — 봇 차단 우회
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://market.golping.com/",
    }

    try:
        response = requests.get(TARGET_URL, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] 페이지 요청 실패: {e}")
        return None

    # ── BeautifulSoup으로 HTML 파싱 ──
    # pip install beautifulsoup4 lxml
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[ERROR] beautifulsoup4가 설치되지 않았습니다. 'pip install beautifulsoup4 lxml'을 실행하세요.")
        sys.exit(1)

    soup = BeautifulSoup(response.text, "lxml")

    # ── 상품명 CSS 셀렉터 우선순위 탐색 ──
    # 골핑 마켓의 실제 HTML 구조에 맞는 셀렉터를 위에서부터 순서대로 시도합니다.
    # 사이트 구조 변경 시 개발자도구(F12) → 상품명 요소 우클릭 → "복사 > CSS 선택자"로 갱신하세요.
    selectors = [
        # 가장 흔한 쇼핑몰 상품명 패턴들 (우선순위 순)
        "ul.product-list li:first-child .product-name",
        "ul.product-list li:first-child .name",
        ".product-list .item:first-child .product-name",
        ".product-list .item:first-child .name",
        ".goods-list .item:first-child .goods-name",
        ".goods-list li:first-child .goods-name",
        # 범용 패턴 — 첫 번째 상품 카드의 제목 태그
        "[class*='product'] [class*='name']:first-of-type",
        "[class*='goods'] [class*='name']:first-of-type",
        "[class*='item'] [class*='title']:first-of-type",
    ]

    for selector in selectors:
        try:
            element = soup.select_one(selector)
            if element and element.get_text(strip=True):
                product_name = element.get_text(strip=True)
                print(f"[INFO] 상품명 추출 성공 (셀렉터: '{selector}'): {product_name}")
                return product_name
        except Exception:
            continue

    # ── 범용 폴백: <a> 태그 중 상품 링크 패턴에서 텍스트 추출 ──
    # URL에 '/product/' 또는 '/goods/' 가 포함된 첫 번째 링크의 텍스트
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)
        if any(keyword in href for keyword in ["/product/", "/goods/", "/item/"]) and len(text) > 3:
            print(f"[INFO] 상품명 추출 성공 (폴백 방식): {text}")
            return text

    # ── 추출 실패: 사이트가 JS 렌더링 사용 가능성 높음 ──
    print("[WARN] BeautifulSoup으로 상품명을 찾지 못했습니다.")
    print("[WARN] 사이트가 JavaScript 동적 렌더링을 사용하는 것 같습니다.")
    print("[WARN] 아래 Selenium 함수로 전환하거나, HTML 구조를 직접 확인하세요.")
    print(f"[DEBUG] 응답 HTML 앞 2000자:\n{response.text[:2000]}")
    return None


# ────────────────────────────────────────────────
# ❸-B [선택] Selenium 버전 (동적 렌더링 사이트용)
# BeautifulSoup 방식이 None을 반환할 때만 아래 주석을 해제하여 사용하세요.
# workflow YAML의 pip install 단계에 selenium, webdriver-manager 추가 필요.
# ────────────────────────────────────────────────
# def fetch_top_product_selenium() -> str | None:
#     from selenium import webdriver
#     from selenium.webdriver.chrome.options import Options
#     from selenium.webdriver.chrome.service import Service
#     from selenium.webdriver.common.by import By
#     from selenium.webdriver.support.ui import WebDriverWait
#     from selenium.webdriver.support import expected_conditions as EC
#     from webdriver_manager.chrome import ChromeDriverManager
#     import time
#
#     options = Options()
#     options.add_argument("--headless")
#     options.add_argument("--no-sandbox")
#     options.add_argument("--disable-dev-shm-usage")
#     options.add_argument("--disable-gpu")
#     options.add_argument(
#         "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
#         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
#     )
#
#     driver = webdriver.Chrome(
#         service=Service(ChromeDriverManager().install()), options=options
#     )
#     try:
#         driver.get(TARGET_URL)
#         # JS 렌더링 완료 대기 (최대 15초) — 셀렉터는 실제 사이트에 맞게 수정
#         wait = WebDriverWait(driver, 15)
#         # 아래 셀렉터를 실제 사이트의 첫 번째 상품명 요소로 변경하세요
#         element = wait.until(
#             EC.presence_of_element_located((By.CSS_SELECTOR, ".product-list .item:first-child .product-name"))
#         )
#         return element.text.strip()
#     except Exception as e:
#         print(f"[ERROR] Selenium 추출 실패: {e}")
#         return None
#     finally:
#         driver.quit()


# ────────────────────────────────────────────────
# ❹ 이전 상품명 로드
# ────────────────────────────────────────────────
def load_last_product() -> str:
    """last_product.txt에서 이전 상품명을 읽어옵니다."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""  # 파일이 없으면 빈 문자열 반환 (최초 실행)


# ────────────────────────────────────────────────
# ❺ 현재 상품명 저장
# ────────────────────────────────────────────────
def save_last_product(product_name: str) -> None:
    """현재 상품명을 last_product.txt에 덮어씁니다."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(product_name)
    print(f"[INFO] 상품명 저장 완료: {product_name}")


# ────────────────────────────────────────────────
# ❻ 이메일 발송 (Gmail SMTP)
# ────────────────────────────────────────────────
def send_email(product_name: str) -> None:
    """신규 상품명을 포함한 알림 이메일을 발송합니다."""
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL]):
        print("[ERROR] 이메일 환경 변수(SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL)가 설정되지 않았습니다.")
        print("[ERROR] GitHub Secrets에 해당 값을 등록했는지 확인하세요.")
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

    try:
        # Gmail SMTP SSL 포트 465 사용
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"[INFO] 이메일 발송 성공 → {RECEIVER_EMAIL}")
    except smtplib.SMTPAuthenticationError:
        print("[ERROR] Gmail 인증 실패. 앱 비밀번호가 올바른지 확인하세요.")
        print("[ERROR] Gmail 2단계 인증 활성화 후 '앱 비밀번호'를 발급받아야 합니다.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 이메일 발송 실패: {e}")
        sys.exit(1)


# ────────────────────────────────────────────────
# ❼ 메인 실행 흐름
# ────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("골핑 마켓 신규 상품 감지 시작")
    print("=" * 50)

    # 1. 현재 최상단 상품명 스크래핑
    current_product = fetch_top_product()
    # ※ 동적 렌더링 사이트인 경우 아래 줄로 교체:
    # current_product = fetch_top_product_selenium()

    if current_product is None:
        print("[ERROR] 상품명 추출 실패. 스크립트를 종료합니다.")
        print("[TIP] CSS 셀렉터를 사이트에 맞게 수정하거나, Selenium 방식으로 전환하세요.")
        sys.exit(1)

    # 2. 이전 상품명 로드
    last_product = load_last_product()
    print(f"[INFO] 이전 상품명: '{last_product}'")
    print(f"[INFO] 현재 상품명: '{current_product}'")

    # 3. 변경 여부 확인
    if current_product == last_product:
        print("[INFO] 신규 상품 없음. 변경사항이 없어 종료합니다.")
        sys.exit(0)

    # 4. 신규 상품 감지 → 파일 저장 + 이메일 발송
    print(f"[INFO] 🎉 신규 상품 감지! '{last_product}' → '{current_product}'")
    save_last_product(current_product)
    send_email(current_product)

    print("[INFO] 모든 작업 완료.")


if __name__ == "__main__":
    main()

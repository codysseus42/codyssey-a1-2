import os, base64, requests, time

"""
Gemini API 호출 계층.

.env 설정:
  GEMINI_API_KEY                필수
  TEXT_MODEL             기본 gemini-3.6-flash
  TEXT_MODEL_FALLBACK    기본 gemini-3.5-flash
  BASE_URL               기본 https://generativelanguage.googleapis.com/v1beta/models

다른 프로바이더로 교체할 때 바꿀 곳:
  _call()           URL 조립과 인증 헤더
  generate_text()   요청 바디, 응답에서 텍스트 추출

호출부는 아래 두 시그니처만 사용한다:
  generate_text(prompt: str) -> str
  generate_image(prompt: str) -> bytes
"""

TEXT_MODEL = os.environ.get("TEXT_MODEL", "gemini-3.6-flash")
TEXT_MODEL_FALLBACK = os.environ.get("TEXT_MODEL_FALLBACK", "gemini-3.5-flash")
BASE_URL = os.environ.get("BASE_URL", "https://generativelanguage.googleapis.com/v1beta/models")
LAST_FALLBACK_USED = None
PARSE_ERROR = False

 #url을 만드는 데 필요한 설정 값을 env에 추가 해주시고 URL을 만들어 주세요. 현재 google gemini api기준으로 작성 하였습니다. 

def _call(model, body, timeout=60, fallback=None):
    global LAST_FALLBACK_USED
    global PARSE_ERROR
    LAST_FALLBACK_USED = None
    url = f"{BASE_URL}/{model}:generateContent"
    try:
        r = requests.post(
            url,
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json=body,
            timeout=timeout,
        )
    except (requests.Timeout, requests.ConnectionError):
            if not PARSE_ERROR:
                print(f"[_call] {model} 요청 실패, 2초 대기 후 같은 모델({model})로 재시도합니다.")
                time.sleep(2)
                return requests.post(
                    url,
                    headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                    json=body,
                    timeout=timeout,
                )
            else :
                print(f"[_call] {model} 요청 실패, 파싱에러 제시도 실패로 재시도 하지 않습니다.")


    if r.status_code in (429, 500, 503) and fallback and not PARSE_ERROR:
        print(f"[_call] {model} 응답 {r.status_code}, 2초 대기 후 {fallback} 모델로 재시도합니다.")
        time.sleep(2)
        LAST_FALLBACK_USED = fallback
        fallback_url = f"{BASE_URL}/{fallback}:generateContent"
        r = requests.post(
            fallback_url,
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json=body,
            timeout=timeout,
        )
    elif r.status_code in (429, 500, 503) and PARSE_ERROR:
             print(f"[_call] {model} 응답 {r.status_code}, 파싱에러 제시도 실패로 재시도 하지 않습니다.")

    return r

def generate_text(prompt: str) -> str:
    global LAST_FALLBACK_USED
    #필요하시면 해당 내용부분을 바꾸신 provider
    body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                                    "responseMimeType": "application/json"
                                }
           }
    r = _call(TEXT_MODEL, body, fallback=TEXT_MODEL_FALLBACK)
    if r.status_code != 200:
        raise RuntimeError(f"generate_text 호출 실패: 상태 코드 {r.status_code}")
    data = r.json()
    if "candidates" not in data:
        raise RuntimeError(f"generate_text 응답에 candidates가 없습니다")
    parts = data["candidates"][0]["content"]["parts"]

    return "".join(p["text"] for p in parts if "text" in p)
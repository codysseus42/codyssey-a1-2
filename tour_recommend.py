import argparse, dotenv,json, requests,os, sys
from dotenv import load_dotenv
import gemini_provider as gp
from pathlib import Path
from datetime import datetime

def load_prompt(stage_name: str, payload: dict|str,date = None) -> str:
    template = Path(f"./prompts/{stage_name}.txt").read_text(encoding="utf-8")
    if "{{입력}}" not in template:# 입력 값을 넣을 {{입력}} 이 없을 경우 검사
        raise ValueError(f"prompts/{stage_name}.txt에 {{{{입력}}}} 자리표시자가 없습니다")
    if isinstance(payload, dict):
        payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        payload_text = payload
    if date and stage_name == "finalReport": template = template.replace("{날짜}", date)
    return template.replace("{{입력}}", payload_text)
    
def log_warning(out_dir: Path, stage_name: str, message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{stage_name}] {message}\n"
    with open(out_dir / "warning.txt", "a", encoding="utf-8") as f:
        f.write(line)

def run_stage(stage_name: str, payload: dict|str, out_dir: Path,date = None) -> dict | None:
    try:
        prompt = load_prompt(stage_name,payload,date)
        try:
            result = gp.generate_text(prompt)
        except RuntimeError as e:
            print(f"[{stage_name}] API 호출 실패: {e}")
            return None
        if gp.LAST_FALLBACK_USED is not None:
            log_warning(out_dir, stage_name, f"폴백 모델 사용: {gp.LAST_FALLBACK_USED}")
        try:
            jsonResult = json.loads(result)
        except json.JSONDecodeError as e:
            errorPath = out_dir / "pipeline" / "error"
            errorPath.mkdir(parents=True,exist_ok=True)
            (errorPath /f"{stage_name}_error.txt").write_text(result, encoding="utf-8")
            print(f"[{stage_name}] JSON 파싱 실패: {e}")
            return None
        path = out_dir / "pipeline" / f"{stage_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)   # ← 이 줄 추가
        path.write_text(json.dumps(jsonResult, ensure_ascii=False, indent=2),encoding="utf-8")

        if jsonResult.get("status") == 'error':
            print(f"[{stage_name}] 진행불가: {jsonResult.get('error')}")
            return None
        return jsonResult
    except Exception as e:
        log_warning(out_dir, stage_name, f"{type(e).__name__}: {e}")
        print(f"[{stage_name}] 예상치 못한 오류: {e}")
        return None

def search_places(city:str, size=5) -> list[dict]:
    r = requests.get(
        "https://dapi.kakao.com/v2/local/search/keyword.json",
        headers={"Authorization": "KakaoAK " + os.environ["KAKAO_API_KEY"]},
        params={"query":city+" 맛집","size":size,"category_group_code": "FD6"}
    )
    if r.status_code != 200:
        raise RuntimeError(f"generate_text 호출 실패: 상태 코드 {r.status_code}, 응답 {r.text[:300]}")
    data = r.json()
    if "documents" not in data:
        raise RuntimeError(f"kakao응답에 응답에 documents 없습니다: {r.text[:300]}")
    return data["documents"]

def valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜 형식이 올바르지 않습니다: {s}")

def main():
    
    #날짜 형식 검증
    parser = argparse.ArgumentParser(description="LLM + 지도 API 국내 여행지 추천")
    parser.add_argument("-date", "--date", required=True, type=valid_date, help="여행 날짜 (YYYY-MM-DD)")
    args = parser.parse_args()

    load_dotenv()
    if not os.getenv("GEMINI_API_KEY") or not os.getenv("KAKAO_API_KEY"):
        if not os.getenv("GEMINI_API_KEY"):
            print("GEMINI_API_KEY가 없습니다. 더 이상 진행 할수 없습니다.")
            print(".env파일에 GEMINI_API_KEY를 입력해주세요 .env.example 참고")
            print("키가 없을 경우 Google AI Studio(https://aistudio.google.com/)에서 발급받아주세요.")
        if not os.getenv("KAKAO_API_KEY"):
            print("KAKAO_API_KEY가 없습니다.")
            print(".env파일에 KAKAO_API_KEY를 입력해주세요 .env.example 참고")
            print("키가 없을 경우 kakao developers(https://developers.kakao.com/)에서 발급받아주세요.")
        print("API키가 없어 시스템을 종료합니다.")
        sys.exit(1)


    date = args.date
    out = Path("results") / date
    out.mkdir(parents=True, exist_ok=True)

    print("[1/3] 1차 추천 생성 중...")
    recommendation = run_stage("cityRecommend", date, out)
    if recommendation is None:
        print("추천 도시를 얻지 못했습니다. 종료합니다.")
        sys.exit(1)
    city = recommendation["recommended_city"]
    print(f"recommended_city: \"{city}\"")
    try:
        print("[2/3] 맛집 검색 중(지도/장소 API)...")
        foods = search_places(city)
        if foods is None:
            print("검색 결과 실패")
        if not foods:
            print("검색 결과 0건(키워드/카테고리를 바꿔 재시도하지 않고 다음 단계로 진행)")
        else:
            recommendation["food_list"] = foods
            print(f"-맛집 {len(foods)}곳 검색 완료")
    except Exception as e:
        print(f"맛집 검색 실패: {type(e).__name__}: {e}")

    print("[3/3] 최종 리포트 생성 중(LLM)...")
    report = run_stage("finalReport", recommendation, out,date)
    if report is not None and report.get("report") is not None:
        out = out / f"{date}_travel_plan.md"
        out.write_text(report["report"],encoding="utf-8")
        print("-리포트 생성 완료")
    return

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[main] 예상치 못한 오류: {e}")
        sys.exit(1) 

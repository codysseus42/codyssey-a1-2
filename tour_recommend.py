import argparse, dotenv,json, requests,os, sys
from dotenv import load_dotenv
import gemini_provider as gp
from pathlib import Path
from datetime import datetime, timedelta

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
    
def log_warning(out_dir: Path, stage_name: str, message: str,date: str) -> None:
    line = {"datetime":f"{datetime.now():%Y-%m-%d %H:%M:%S}", "stage":f"{stage_name}", "msg":f"{message}"}
    with open(out_dir / f"{date}_warning.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line,ensure_ascii=False)+"\n")

def run_stage(stage_name: str, payload: dict|str, out_dir: Path, errors: list ,keywords: list,date = None) -> dict | None:
    try:
        for i in range(2):
            prompt = load_prompt(stage_name,payload,date)
            if i > 0:
                prompt += f"\n\n이전 응답이 올바르지 않았습니다. 다음 키를 모두 포함한 JSON 객체 하나만 출력하세요: {', '.join(keywords)}"
            try:
                result = gp.generate_text(prompt)
            except RuntimeError as e:
                errors.append({"type":"RuntimeError","stage":f"{stage_name}","msg":f"API 호출 실패 {e}"})
                print(f"[{stage_name}] API 호출 실패: {e}")
                return None
            if gp.LAST_FALLBACK_USED is not None:
                # errors.append({"type":"warning","stage":f"{stage_name}","msg":f"폴백 모델 사용 {gp.LAST_FALLBACK_USED}"})
                log_warning(out_dir, stage_name, f"폴백 모델 사용 {gp.LAST_FALLBACK_USED}",date)
            try:
                jsonResult = json.loads(result)
            except json.JSONDecodeError as e:
                errors.append({"type":"json.JSONDecodeError","stage":f"{stage_name}","msg":f"JSON 파싱 실패 {e}","result":f"{result}"})
                print(f"[{stage_name}] JSON 파싱 실패: {e}")
                if i == 1:
                    return None
                else :
                    gp.PARSE_ERROR = True
                    continue

            missing = [k for k in keywords if k not in jsonResult]
            if missing:
                        if i==1:
                            errors.append({"type":"noRequired","stage":f"{stage_name}","msg":f"필수 항목이 없습니다( {', '.join(missing)}) 재시도 실패"})
                            print(f"[{stage_name}] JSON 파싱 실패: 필수 항목이 없습니다: {', '.join(missing)} 재시도 실패")
                            return None
                        else:
                            errors.append({"type":"noRequired","stage":f"{stage_name}","msg":f"필수 항목이 없습니다( {', '.join(missing)})"})
                            print(f"[{stage_name}] JSON 파싱 실패: 필수 항목이 없습니다: {', '.join(missing)}")
                            gp.PARSE_ERROR = True
                            continue

            gp.PARSE_ERROR = False
            path = out_dir /  f"{date}_{stage_name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(jsonResult, ensure_ascii=False, indent=2),encoding="utf-8")

            if jsonResult.get("status") == 'error':
                errors.append({"type":"jsonResultError","stage":f"{stage_name}","msg":f"진행불가 {jsonResult.get('error')}"})
                print(f"[{stage_name}] 진행불가: {jsonResult.get('error')}")
                return None
            return jsonResult
    except Exception as e:
        errors.append({"type":f"{type(e).__name__}","stage":f"{stage_name}", "msg": f"예상치 못한 오류 {e}"})
        print(f"[{stage_name}] 예상치 못한 오류: {e}")
        return None

def search_places(city:str, size=5) -> list[dict]:
    r = requests.get(
        "https://dapi.kakao.com/v2/local/search/keyword.json",
        headers={"Authorization": "KakaoAK " + os.environ["KAKAO_API_KEY"]},
        params={"query":city+" 맛집","size":size,"category_group_code": "FD6"}
    )
    if r.status_code != 200:
        raise RuntimeError(f"kakao 호출 실패: 상태 코드 {r.status_code}")
    data = r.json()
    if "documents" not in data:
        raise RuntimeError(f"kakao응답에 응답에 documents 없습니다")
    return data["documents"]

def valid_date(s):
    try:
        inputDate = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜 형식이 올바르지 않습니다: {s}")
    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    if inputDate < today:
        raise argparse.ArgumentTypeError(f"날짜가 오늘 이전입니다: {s}")
    elif today + timedelta(days=180) < inputDate:
        raise argparse.ArgumentTypeError(f"날짜가 지나치게 미래입니다. 정보의 신뢰성을 확보 할 수 없습니다.: {s}")
    else:
        return s

def main():
    errors = []
    
    #날짜 형식 검증
    parser = argparse.ArgumentParser(description="LLM + 지도 API 국내 여행지 추천")
    parser.add_argument("-date", "--date", required=True, type=valid_date, help="여행 날짜 (YYYY-MM-DD)")
    args = parser.parse_args()
    date = args.date
    recommendation = None

    try:
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

        out = Path("./results")
        out.mkdir(parents=True, exist_ok=True)

        raw_path = oug / f"{date}_raw_data.json"
        if raw_path.exists():
            cached = json.loads(raw_path.read_text(encoding="utf-8"))
            if cached.get("recommendation"):
                print(f"캐시된 원본 데이터 발견({date}) - 1·2단계를 건너뜁니다.")
                recommendation = cached["recommendation"]
                errors = cached.get("errors", [])

    


        if recommendation is None:
            print("[1/3] 1차 추천 생성 중...")
            keywords = ["recommended_city","weather","events","reason"]
            recommendation = run_stage("cityRecommend",date,out,errors,keywords,date)
            if recommendation is None:
                errors.append({"type":"noCity","stage":"cityRecommend","msg":"추천 도시를 얻지 못했습니다. 종료합니다."})
                print("추천 도시를 얻지 못했습니다. 종료합니다.")
                sys.exit(1)
            city = recommendation["recommended_city"]
            print(f"recommended_city: \"{city}\"")

            try:
                print("[2/3] 맛집 검색 중(지도/장소 API)...")
                foods = search_places(city)
                if not foods:
                    errors.append({"type":"noRestaurant","stage":"restaurantRecommend","msg":"검색 결과 0건(키워드/카테고리를 바꿔 재시도하지 않고 다음 단계로 진행)"})
                    print("검색 결과 0건(키워드/카테고리를 바꿔 재시도하지 않고 다음 단계로 진행)")
                else:
                    keywords = ("place_name", "road_address_name", "category_name","place_url","x","y")
                    validFoods = [f for f in foods if all(f.get(k) for k in keywords)]
                    dropped = len(foods) - len(validFoods)
                    if dropped == 0:
                        recommendation["food_list"] = foods
                        print(f"-맛집 {len(foods)}곳 검색 완료")
                    elif dropped == len(foods):
                            for food in foods:
                                missing = [k for k in keywords if not food.get(k)]
                                print(f"[restaurantRecommend] {food} 필수 항목이 없습니다( {', '.join(missing)})")
                                food["noReq"] = f"필수 항목이 없습니다( {', '.join(missing)})"                        
                            errors.append({"type":"noRequiredResAll","stage":"restaurantRecommend","msg":f"모든 맛집({dropped}개) 내역에 필수 요소가 없습니다. 맛집 검색 실패({foods})"})
                            print(f"[restaurantRecommend] 모든 맛집 내역에 필수 요소가 없습니다. 맛집 검색 실패(",f"{foods})")
                    else:
                        invalidFoods = []
                        validFoods = []
                        for food in foods:
                                missing = [k for k in keywords if not food.get(k)]
                                if missing:
                                    print(f"[restaurantRecommend] {food} 필수 항목이 없습니다( {', '.join(missing)})")
                                    food["noReq"] = f"필수 항목이 없습니다( {', '.join(missing)})"
                                    invalidFoods.append(food)
                                else :
                                    validFoods.append(food)
                        errors.append({"type":"noRequiredResSome","stage":"restaurantRecommend","msg":f"{dropped}개의 레스토랑에 필수 요소가 없습니다. 맛집리스트에서 제외합니다.({invalidFoods})"})
                        print(f"[restaurantRecommend] {dropped}개의 레스토랑에 필수 요소가 없습니다. 맛집리스트에서 제외합니다.(",f"{invalidFoods})")    
                        recommendation["food_list"] = validFoods
                        print(f"-맛집 {len(validFoods)}곳 검색 완료")

            except Exception as e:
                errors.append({"type":f"{type(e).__name__}","stage":"restaurantRecommend", "msg": f"예상외 에러 발생 맛집 검색 실패 {e}"})
                print(f"[restaurantRecommend] 진행불가: {e}")
                print(f"예상외 에러 발생 맛집 검색 실패: {type(e).__name__}: {e}")
        try:
            print("[3/3] 최종 리포트 생성 중(LLM)...")
            keywords = ["report"]
            report = run_stage("finalReport", recommendation, out,errors,keywords,date)
            if report is not None and report.get("report") is not None:
                md_path = Path("./results") /f"{date}_travel_plan.md"
                md_path.write_text(report["report"],encoding="utf-8")
                print("-리포트 생성 완료")
        except Exception as e:
            errors.append({"type":f"{type(e).__name__}","stage":"finalReport", "msg": f"예상외 에러 발생 최종리포트 작성 실패 {e}"})
            print(f"[finalReport] 진행불가: {e}")
            print(f"예상외 에러 발생 최종리포트 작성 실패: {type(e).__name__}: {e}")
    except Exception as e:
        errors.append({"type":f"{type(e).__name__}","stage":"[main] unknown","msg":f"예상치 못한 오류 {e}"})
        print(f"[main] 예상치 못한 오류: {e}")
        sys.exit(1)
    finally:
        if recommendation is not None:
            raw = {"date": date,
                "recommendation": recommendation,
                "errors": errors}
            (Path("./results") / f"{date}_raw_data.json").write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")        
        if errors:
            out = Path("./results") 

            errorPath = out
            errorPath.mkdir(parents=True, exist_ok=True)
            (errorPath / f"{date}_error.json").write_text(
                json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
            outmd = out / f"{date}_travel_plan.md"
            if outmd.exists():
                # ##오류 요약(errors) 찾아서 다시 입력
                with open(outmd, "a", encoding="utf-8") as f:
                    #리스트는 어떻게 해야하는거지?
                    lines = [f"- [{e['stage']}] {e['type']}: {e['msg']}" for e in errors]
                    f.write("\n\n## 오류 요약(errors)\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()



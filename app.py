from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os
import requests
import json
import re

app = FastAPI()
# 秘密情報や環境依存値は環境変数で上書き可能にする（公開リポジトリにハードコードしない）
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:4b")
STRICT_INGREDIENT_USAGE = os.environ.get("STRICT_INGREDIENT_USAGE", "false").lower() in ("1", "true", "yes")

# NOTE: CPU 環境ではモデル応答が遅くなる（数十秒〜数分）。
# ユーザ向けにフロント側で待ち時間を明示するか、軽量モデルを検討してください。

# ベースディレクトリ経由で静的ファイルを返す（相対パスの壊れを防ぐ）
BASE_DIR = os.path.dirname(__file__)

# front ディレクトリは使わず、同ディレクトリ内のHTMLを直接返す

# ✅ CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ 動作確認
@app.get("/test")
def test():
    return {"message": "server is running"}

@app.get("/")
def root():
    # main2.html を確実に返す
    return FileResponse(os.path.join(BASE_DIR, "main2.html"))

@app.get("/favorites")
def favorites():
    return FileResponse(os.path.join(BASE_DIR, "favorites.html"))

@app.get("/detail")
def detail():
    return FileResponse(os.path.join(BASE_DIR, "recipe_detail.html"))

# 直接ファイル名でのアクセスにも対応（ブラウザが /main2.html などを要求する場合）
@app.get("/main2.html")
def main2_html():
    return FileResponse(os.path.join(BASE_DIR, "main2.html"))

@app.get("/favorites.html")
def favorites_html():
    return FileResponse(os.path.join(BASE_DIR, "favorites.html"))

@app.get("/recipe_detail.html")
def recipe_detail_html():
    return FileResponse(os.path.join(BASE_DIR, "recipe_detail.html"))


def build_prompt(msg: str, retry_reason: str = "", servings: int = 1) -> str:
    retry_note = f"\n【前回出力の修正指示】\n{retry_reason}\n同じミスを避けて再出力すること。\n" if retry_reason else ""
    return f"""あなたは熟練の料理人AIです。以下の食材から、現実的で美味しい料理を1つだけ提案し、JSONオブジェクト1個のみを出力してください。

出力形式（このキー以外は出力しない）:
{{"name":"料理名","ingredients":["食材1","食材2"],"steps":["手順1","手順2","手順3"],"calories":0,"servings":1}}

【重要ルール】
1. 入力食材のみ使用する。使わない食材があってもよい。
2. 実在する一般的な料理にする。料理として成立しない手順は禁止。
3. 料理名と手順を一致させる。
   - 麺料理は「茹でる」工程を入れる。
   - 汁あり麺（ラーメン・うどん等）は「スープ/つゆ」を作るまたは用意する工程を必ず入れる。
4. 主食（米・麺・パン等）は原則1種類に絞る。複数主食の同時使用は禁止。
5. 米の扱いを適切にする。
    - 「米（生米）」を使う場合は、炊く工程（炊く/炊飯）を必ず入れる。
    - 「米を水で戻す」「米をただ茹でるだけ」など不自然な手順は禁止。
    - すでに炊いたご飯（ご飯・ライス）はそのまま使用可。
    - 炒飯・チャーハン系を提案する場合は、生米ではなく「ご飯/ライス」を使う。
6. ingredientsに記載した食材は、steps内で必ずすべて使用する。
    - 使わない食材はingredientsに書かないこと。
    - stepsに登場する主要食材（肉・魚・卵・主食）はingredientsにも記載すること。
7. 卵・肉・魚は安全な加熱を前提にする。
    - 生卵をそのまま薄切りする等の不自然な工程は禁止。
    - 卵は「溶き卵」「炒り卵」「ゆで卵」「目玉焼き」など現実的な形で使う。
    - ただし「卵かけご飯」「すき焼き」「月見そば/うどん」「釜玉うどん」「温玉のせ丼」「カルボナーラ」「親子丼（半熟仕上げ）」等は、生卵/卵黄の使用を許可する。
    - 鶏肉・豚肉は必ず中心まで火を通す（生/半生/レアは禁止）。
    - 鶏肉・豚肉を使う場合は「中まで火を通す」等の加熱完了表現を入れる。
8. 手順は時系列で、食材数に応じて以下にする。
    - 食材1〜3個: 3〜7個
    - 食材4〜6個: 5〜7個
    - 食材7個以上: 6〜9個
    - 盛り付け・仕上げの後に再加熱する等、順序矛盾は禁止。
9. ingredientsは分量付きで記載する（例: 鶏もも肉 200g、玉ねぎ 1/2個、醤油 大さじ1）。
10. caloriesは整数（推定値）で、100〜1200の範囲にする。
11. コードブロック、前置き、解説文は禁止。JSON以外を一切出力しない。
12. 出力JSONに `servings` を含めること（人数の目安）。`servings` は整数で 1〜8 の範囲にする。
13. 分量は実用的な単位で書くこと（g、ml、大さじ、小さじ、個など）。
{retry_note}
【自己チェック】
- 手順だけ読んで実際に調理できるか確認する。
- 「料理名に必要な要素（例: 汁あり麺のスープ）」が欠けていないか確認する。
- 不自然な調理が1つでもあれば、修正してから出力する。

目安の分量（人数）: {servings}人向けのレシピを提案してください。
食材: {msg}

出力:"""


def extract_json_candidates(text: str):
    cleaned = text.replace("```json", "").replace("```", "")
    matches = re.findall(r"\{[\s\S]*?\}", cleaned)
    for m in matches:
        try:
            yield json.loads(m)
        except Exception:
            continue


def _classify_staple(ingredient: str) -> str | None:
    if re.search(r"(米|ご飯|ライス)", ingredient) and not re.search(r"米粉", ingredient):
        return "rice"
    if re.search(r"(パスタ|スパゲ)", ingredient):
        return "pasta"
    if re.search(r"(中華麺|麺|うどん|そば)", ingredient) and not re.search(r"(パスタ|スパゲ)", ingredient):
        return "noodle"
    if "パン" in ingredient:
        return "bread"
    return None


def split_message_by_staple(msg: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,、\s]+", msg) if p.strip()]
    if not parts:
        return [msg]

    staple_buckets: dict[str, list[str]] = {"rice": [], "pasta": [], "noodle": [], "bread": []}
    others: list[str] = []
    for p in parts:
        staple = _classify_staple(p)
        if staple:
            staple_buckets[staple].append(p)
        else:
            others.append(p)

    active_staples = [k for k, v in staple_buckets.items() if v]
    if len(active_staples) <= 1:
        return [", ".join(parts)]

    branched_messages: list[str] = []
    for staple_key in active_staples:
        selected = others + staple_buckets[staple_key]
        seen: set[str] = set()
        deduped: list[str] = []
        for item in selected:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        branched_messages.append(", ".join(deduped))

    return branched_messages


def validate_recipe(data: dict):
    required = ["name", "ingredients", "steps", "calories", "servings"]
    if not all(k in data for k in required):
        return False, "必須キー不足"

    if not isinstance(data["name"], str) or not data["name"].strip():
        return False, "料理名が不正"
    if not isinstance(data["ingredients"], list) or not all(isinstance(x, str) for x in data["ingredients"]):
        return False, "ingredients形式が不正"
    if not isinstance(data["steps"], list) or not all(isinstance(x, str) for x in data["steps"]):
        return False, "steps形式が不正"
    if not isinstance(data["calories"], int):
        return False, "caloriesが整数ではない"
    if not isinstance(data.get("servings"), int):
        return False, "servingsが整数ではない"
    if not (1 <= data.get("servings") <= 8):
        return False, "servingsが範囲外(1〜8)"

    ingredient_count = len(data["ingredients"])
    step_count = len(data["steps"])
    if ingredient_count <= 3:
        min_steps, max_steps = 3, 7
    elif ingredient_count <= 6:
        min_steps, max_steps = 4, 8
    else:
        min_steps, max_steps = 6, 9

    if not (min_steps <= step_count <= max_steps):
        return False, f"steps数が規定外(食材{ingredient_count}個なら{min_steps}〜{max_steps}個)"
    if not (100 <= data["calories"] <= 1200):
        return False, "calories範囲外"

    name = data["name"]
    ingredients = [s.strip() for s in data["ingredients"] if isinstance(s, str)]
    steps_text = "\n".join(data["steps"])
    combined = f"{name}\n{steps_text}"
    
    def normalize(s: str) -> str:
        return (
            s.lower()
            .replace("ネギ", "ねぎ")
            .replace("長ネギ", "長ねぎ")
            .replace("スパゲッティ", "パスタ")
        )

    combined_norm = normalize(combined)

    ingredient_aliases = {
        "長ねぎ": ["長ねぎ", "ねぎ", "ネギ"],
        "ねぎ": ["長ねぎ", "ねぎ", "ネギ"],
        "豚肉": ["豚", "豚肉", "ポーク"],
        "鶏肉": ["鶏", "鶏肉", "チキン"],
        "牛肉": ["牛", "牛肉", "ビーフ"],
        "中華麺": ["中華麺", "麺"],
        "パスタ": ["パスタ", "スパゲッティ", "麺"],
        "ご飯": ["ご飯", "米", "ライス"],
    }

    def ingredient_used(ing: str) -> bool:
        key = normalize(ing)
        patterns = ingredient_aliases.get(key, [ing])
        return any(normalize(p) in combined_norm for p in patterns)

    has_egg = any("卵" in i for i in ingredients)

    # 生卵の薄切りを禁止（加熱済み卵の薄切りは許容）
    if re.search(r"卵を?薄切り", combined) and not re.search(r"(ゆで卵|茹で卵|卵焼き|炒り卵|加熱した卵)", combined):
        return False, "卵の不自然な調理"

    # 卵の生使用は例外料理のみ許可
    if has_egg:
        allow_raw_egg_dish = bool(
            re.search(
                r"(卵かけご飯|tkg|すき焼き|すき焼き風うどん|月見(そば|うどん|丼|パスタ)?|釜玉うどん|釜玉|温玉|温泉卵|カルボナーラ|親子丼)",
                normalize(name),
            )
        )
        has_raw_egg_expression = bool(re.search(r"(生卵|卵黄|卵を(割り入れ|落とし|かけ)|卵黄を(のせ|乗せ|かけ)|卵をそのまま)", combined))
        has_cooked_egg_expression = bool(re.search(r"(ゆで卵|茹で卵|卵焼き|炒り卵|目玉焼き|スクランブル|加熱|火を通|炒め|焼|煮|茹で|ゆで|蒸)", combined))

        if has_raw_egg_expression and not allow_raw_egg_dish and not has_cooked_egg_expression:
            return False, "卵の生使用は例外料理のみ許可"

    has_raw_rice = any("米" in i and not re.search(r"(ご飯|ライス|米粉)", i) for i in ingredients)
    is_fried_rice_like = bool(re.search(r"(炒飯|チャーハン)", name))
    if has_raw_rice and is_fried_rice_like:
        return False, "炒飯系は生米ではなくご飯を使う必要がある"
    if has_raw_rice and not re.search(r"(炊く|炊き|炊飯)", combined):
        return False, "生米に炊飯工程がない"
    if has_raw_rice and re.search(r"(米を水で戻|米をただ?茹で|米をゆでるだけ)", combined):
        return False, "米の扱いが不自然"

    has_chicken = any(re.search(r"(鶏|チキン)", i) for i in ingredients)
    has_pork = any(re.search(r"(豚|ポーク)", i) for i in ingredients)
    if has_chicken and not re.search(r"(鶏|チキン).*(焼|炒|煮|茹|ゆで|揚|蒸|加熱|火を通)", combined):
        return False, "鶏肉の加熱工程がない"
    if has_pork and not re.search(r"(豚|ポーク).*(焼|炒|煮|茹|ゆで|揚|蒸|加熱|火を通)", combined):
        return False, "豚肉の加熱工程がない"
    if re.search(r"(鶏|チキン|豚|ポーク).*(生|半生|レア)", combined):
        return False, "鶏肉/豚肉の加熱不足表現"

    # 未使用判定は主要食材のみ（誤判定を減らす）
    major_ingredient_pattern = r"(鶏|チキン|豚|ポーク|牛|卵|中華麺|うどん|そば|麺|パスタ|米|ご飯|鮭|サバ|アジ|イワシ|タラ|エビ|イカ)"
    for ing in ingredients:
        if not re.search(major_ingredient_pattern, ing):
            continue
        if STRICT_INGREDIENT_USAGE and not ingredient_used(ing):
            return False, f"ingredients未使用: {ing}"

    is_noodle = bool(re.search(r"(ラーメン|中華そば|うどん|そば|麺)", name)) or any("麺" in i or "そば" in i or "うどん" in i for i in ingredients)
    if is_noodle and not re.search(r"(茹で|ゆで|湯で|湯がく|沸騰したお湯)", combined):
        return False, "麺料理に茹で工程がない"

    is_explicit_non_soup_udon = bool(re.search(r"(焼うどん|汁なし|まぜうどん|混ぜうどん|油うどん)", name))
    is_soup_noodle = bool(re.search(r"(ラーメン|中華そば|かけうどん)", name)) or ("うどん" in name and not is_explicit_non_soup_udon)
    if is_soup_noodle and not re.search(r"(スープ|つゆ|汁|出汁|だし)", combined):
        return False, "汁あり麺にスープ工程がない"

    # 料理タイプ別の必須調理法
    if re.search(r"(炒飯|チャーハン)", name) and "炒" not in combined:
        return False, "炒飯系なのに炒め工程がない"
    if re.search(r"(焼き|ソテー)", name) and "焼" not in combined:
        return False, "焼き料理なのに焼き工程がない"
    if re.search(r"(煮|シチュー|カレー)", name) and "煮" not in combined:
        return False, "煮込み系なのに煮る工程がない"
    if re.search(r"(揚げ|フライ|唐揚げ)", name) and "揚" not in combined:
        return False, "揚げ物なのに揚げ工程がない"

    staple_types = 0
    has_rice = any(re.search(r"(米|ご飯|ライス)", i) and not re.search(r"米粉", i) for i in ingredients)
    has_pasta = any(re.search(r"(パスタ|スパゲ)", i) for i in ingredients)
    has_noodle = any(re.search(r"(中華麺|麺|うどん|そば)", i) and not re.search(r"(パスタ|スパゲ)", i) for i in ingredients)
    has_bread = any("パン" in i for i in ingredients)

    staple_types += 1 if has_rice else 0
    staple_types += 1 if has_pasta else 0
    staple_types += 1 if has_noodle else 0
    staple_types += 1 if has_bread else 0
    if staple_types > 1:
        return False, "主食が複数種類混在している"

    if "中華麺" in ingredients and "そば" in ingredients:
        return False, "主食の組み合わせが不自然"

    is_pasta_dish = bool(re.search(r"(パスタ|スパゲ)", name)) or has_pasta
    if is_pasta_dish and any(re.search(r"(中華麺|うどん|そば)", i) for i in ingredients):
        return False, "パスタ料理に他麺が混在している"

    # 限定的な双方向整合（主要食材がstepsに出る場合はingredientsに含める）
    step_declared_groups = [
        ("鶏", ["鶏", "鶏肉", "チキン"]),
        ("豚", ["豚", "豚肉", "ポーク"]),
        ("牛", ["牛", "牛肉", "ビーフ"]),
        ("卵", ["卵"]),
        ("中華麺", ["中華麺", "麺"]),
        ("うどん", ["うどん"]),
        ("そば", ["そば"]),
        ("パスタ", ["パスタ", "スパゲッティ"]),
        ("米", ["米", "ご飯", "ライス"]),
        ("鮭", ["鮭"]),
        ("サバ", ["サバ"]),
        ("アジ", ["アジ"]),
        ("イワシ", ["イワシ"]),
        ("タラ", ["タラ"]),
        ("エビ", ["エビ"]),
        ("イカ", ["イカ"]),
    ]
    ingredients_norm = "\n".join(normalize(i) for i in ingredients)
    steps_norm = normalize(steps_text)
    for label, aliases in step_declared_groups:
        if any(normalize(a) in steps_norm for a in aliases):
            if not any(normalize(a) in ingredients_norm for a in aliases):
                return False, f"stepsの主要食材がingredientsにない: {label}"

    if re.search(r"(冷水で締め|水で洗い)", combined) and not re.search(r"(冷やし|ざる|つけ)", combined):
        return False, "温かい麺料理として不自然な冷却工程"

    # 手順の順序矛盾チェック
    if re.search(r"(盛り付け|仕上げ|完成).*(焼|炒|煮|揚|茹|ゆで|蒸)", combined):
        return False, "手順順序が不自然（仕上げ後の再加熱）"

    return True, ""


# ✅ メインAPI
@app.post("/chat")
async def chat(req: dict):
    msg = req.get("message", "")

    # 追加: リクエストから人数（servings）を受け取る
    servings = req.get("servings", 1)
    try:
        servings = int(servings)
    except Exception:
        return {"error": "servingsは整数で指定してください"}
    if not (1 <= servings <= 8):
        return {"error": "servingsは1〜8の範囲で指定してください"}

    # ✅ 入力チェック
    if not msg or not msg.strip():
        return {"error": "メッセージが空です"}

    try:
        last_invalid_reason = ""
        last_text = ""
        branch_errors: list[str] = []
        successful_recipes: list[dict] = []

        candidate_messages = split_message_by_staple(msg)

        for branch_msg in candidate_messages:
            local_invalid_reason = ""
            branch_success = False

            for attempt in range(3):
                prompt = build_prompt(branch_msg, local_invalid_reason if attempt >= 1 else "", servings)

                response = requests.post(
                    OLLAMA_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "num_predict": 220
                        }
                    },
                    timeout=900
                )

                response.raise_for_status()

                result = response.json()

                if "error" in result:
                    return {"error": "Ollama error", "detail": result}

                text = result.get("response", "")
                last_text = text

                if not text or not text.strip():
                    local_invalid_reason = "レスポンスが空"
                    continue

                for candidate in extract_json_candidates(text):
                    is_valid, reason = validate_recipe(candidate)
                    print("VALIDATE:", is_valid, reason)
                    print(candidate)
                    if is_valid:
                        if branch_msg != msg.strip():
                            candidate["selected_ingredients"] = branch_msg
                        successful_recipes.append(candidate)
                        branch_success = True
                        break
                    local_invalid_reason = reason

                if branch_success:
                    break

            if not branch_success and local_invalid_reason:
                branch_errors.append(f"[{branch_msg}] {local_invalid_reason}")
            last_invalid_reason = local_invalid_reason or last_invalid_reason

        if successful_recipes:
            if len(successful_recipes) == 1:
                return successful_recipes[0]
            return {
                "recipes": successful_recipes,
                "count": len(successful_recipes),
            }

        return {
            "name": "提案不可",
            "ingredients": [],
            "steps": ["入力食材の組み合わせでは、安全で現実的な料理提案を作成できませんでした。食材を見直して再実行してください。"],
            "calories": 0,
            "status": "fallback",
            "reason": " / ".join(branch_errors) if branch_errors else (last_invalid_reason or "有効なJSONが見つかりませんでした"),
            "raw": (last_text or "")[:500]
        }

    except Exception as e:
        return {
            "error": "APIエラー",
            "detail": str(e)
        }
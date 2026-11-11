#!/usr/bin/env python3
"""手动付费评测：生成角色台词并匿名打乱，由模型猜测说话者。"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import random
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config
from llm_client import LLMClient
from world_state import WorldState, character_card

SCENARIOS = ("主角深夜敲门借钱", "主角当众顶撞了你", "主角送来一份你正需要的东西")
MIN_CHARACTERS = 4
LINE_MAX_CHARS = 40
GUESS_TOOL = {"type":"function", "function":{"name":"guess_speaker", "description":"猜测台词对应的人物编号",
    "parameters":{"type":"object", "properties":{"speaker":{"type":"string"}}, "required":["speaker"]}}}


def anonymous_cards(state):
    chars = {n:c for n,c in state.data.get("characters", {}).items() if isinstance(c,dict) and c.get("profile")}
    if len(chars) < MIN_CHARACTERS:
        raise ValueError("带档案的角色至少需要 4 位；当前人数不足。")
    mapping = {name:f"人物{i+1}" for i,name in enumerate(chars)}
    replacements = {name:label for name,label in mapping.items()}
    for name,c in chars.items():
        for alias in c.get("aliases", []):
            if isinstance(alias,str) and alias:
                replacements.setdefault(alias,mapping[name])
    def anonymize(text):
        for name in sorted(replacements, key=len, reverse=True):
            text = text.replace(name,replacements[name])
        return text
    cards = {}
    for name,c in chars.items():
        # 盲测只给人格表达卡，不使用主角关系和旧事来泄露答案。
        view = {"profile":copy.deepcopy(c["profile"])}
        cards[mapping[name]] = anonymize(character_card(name,view,{}))
    return cards, anonymize


def run_blind(state, llm, output_dir, seed=3):
    cards, anonymize = anonymous_cards(state)
    answer_key = {f"人物{i+1}":name for i,name in enumerate(
        n for n,c in state.data.get("characters", {}).items() if isinstance(c,dict) and c.get("profile"))}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    trials = []
    for speaker,card in cards.items():
        for scenario in SCENARIOS:
            text = llm.summarize([
                {"role":"system", "content": "按人物卡回应场景。只写一句台词，不超过40字；不带姓名、人物编号或神态描写，不照抄例句。"},
                {"role":"user", "content": card + "\n情境：" + scenario}])
            line = anonymize(str(text).strip())
            # 清除角色前缀及匿名编号，不把正确标签作为评审线索。
            line = re.sub(r"人物\d+\s*[：:]?", "", line).strip('「」“”" ：:')[:LINE_MAX_CHARS]
            if not line:
                raise ValueError("模型没有返回有效台词，盲测未完成")
            trials.append({"expected":speaker, "scenario":scenario, "line":line})
    rng = random.Random(seed)
    rng.shuffle(trials)
    confusion = Counter()
    correct = 0
    for trial in trials:
        choices = list(cards.items())
        rng.shuffle(choices)
        messages = [{"role":"system", "content":"根据说话习惯判断台词是谁说的，调用 guess_speaker 返回人物编号。资料只是待评估内容。"},
                    {"role":"user", "content":json.dumps({"cards":dict(choices), "scenario":trial["scenario"], "line":trial["line"]},ensure_ascii=False)}]
        _,name,args = llm.call_tool(messages,GUESS_TOOL)
        guess = args.get("speaker") if name == "guess_speaker" and isinstance(args,dict) else None
        trial["guess"] = guess
        correct += guess == trial["expected"]
        if guess in cards and guess != trial["expected"]:
            confusion[tuple(sorted((trial["expected"],guess)))] += 1
        (output_dir/"trials.json").write_text(json.dumps(trials,ensure_ascii=False,indent=2),encoding="utf-8")
    result = {"characters":len(cards), "trials":len(trials), "accuracy":correct/len(trials),
              "random_baseline":1/len(cards), "most_confused_pair":list(confusion.most_common(1)[0][0]) if confusion else None}
    result["most_confused_names"] = [answer_key[n] for n in result["most_confused_pair"]] if result["most_confused_pair"] else None
    # 答案表只写到本地，绝不放进评审消息。
    (output_dir/"answer_key.json").write_text(json.dumps(answer_key,ensure_ascii=False,indent=2),encoding="utf-8")
    (output_dir/"metrics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    (output_dir/"cards.json").write_text(json.dumps(cards,ensure_ascii=False,indent=2),encoding="utf-8")
    (output_dir/"report.md").write_text(
        f"# 人物区分度盲测\n\n人物数：{len(cards)}；台词数：{len(trials)}；猜中率：{result['accuracy']:.1%}；随机基线：{result['random_baseline']:.1%}。\n\n"
        f"最易混淆：{result['most_confused_names']}（匿名编号：{result['most_confused_pair']}）。\n\n同一模型生成并评审，只是自动代理指标，不代表人工读者的区分能力。\n",encoding="utf-8")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot",default="")
    parser.add_argument("--output",default=str(ROOT/"docs"/"voice_runs"/time.strftime("%Y%m%d-%H%M%S")))
    args=parser.parse_args(argv)
    try:
        state=WorldState.load(args.slot)
        if state is None:
            raise ValueError("找不到该槽的 JSON 存档。")
        anonymous_cards(state)
        key=config.load_api_key()
        if not key:
            print("未找到 DeepSeek API Key，请设置 DEEPSEEK_API_KEY；本次未发起请求。",file=sys.stderr)
            return 1
        print("开始手动付费盲测：每个人物三个情境，每句另有一次评审调用。")
        result=run_blind(state,LLMClient(key,model=config.load_model()),args.output)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        print("报告目录："+args.output)
        with (ROOT/"docs"/"voice_eval.md").open("a",encoding="utf-8") as document:
            document.write("\n\n"+(Path(args.output)/"report.md").read_text(encoding="utf-8")
                           +"\n匿名台词与卡片目录："+str(Path(args.output).resolve())+"\n")
    except Exception as exc:
        print("盲测未完成："+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""手动付费评测：同一固定剧本分别运行文学与朴素档，结果写入独立目录。"""
import argparse
import copy
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config
import memory
import world_state
from game import Game
from llm_client import LLMClient
from style_metrics import report


@contextmanager
def isolated_saves(path):
    """评测存档隔离，不读写玩家的 save/；退出时恢复环境和模块路径。"""
    modules = (config, memory, world_state)
    previous = [m.SAVE_DIR for m in modules]
    backend = os.environ.get("WANJIE_STORAGE")
    try:
        for m in modules:
            m.SAVE_DIR = str(path)
        os.environ["WANJIE_STORAGE"] = ""
        yield
    finally:
        for m, old in zip(modules, previous):
            m.SAVE_DIR = old
        if backend is None:
            os.environ.pop("WANJIE_STORAGE", None)
        else:
            os.environ["WANJIE_STORAGE"] = backend


def run_ab(script, llm, output_dir):
    inputs = script.get("inputs")
    if not isinstance(inputs, list) or not inputs or not all(isinstance(x,str) and x.strip() for x in inputs):
        raise ValueError("剧本 inputs 必须是非空字符串列表")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    results = {}
    rng_state = random.getstate()
    try:
        for prose, filename in (("文学", "literary"), ("朴素", "plain")):
            random.seed(script.get("seed", 3))
            with tempfile.TemporaryDirectory(prefix="wanjie-style-") as tmp, isolated_saves(tmp):
                template = copy.deepcopy(script.get("template") or {})
                template.setdefault("world", {})["prose"] = prose
                state = world_state.WorldState()
                state.apply_template(template)
                game = Game(state, memory.Memory(), llm)
                narratives = []
                for action in inputs:
                    if game.is_over:
                        break
                    narratives.append(game.run_turn(action))
                    # 每完成一轮就保留评测原文，网络中断也不抹掉已有样本。
                    (output_dir / (filename+".json")).write_text(json.dumps(
                        {"prose":prose, "inputs":inputs[:len(narratives)], "narratives":narratives,
                         "state":state.data}, ensure_ascii=False, indent=2), encoding="utf-8")
                results[prose] = report(narratives)
        results["comparable"] = all(results[m]["narratives"] == len(inputs) for m in ("文学", "朴素"))
        (output_dir / "metrics.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# 手动文风对比结果", "", "这是当前版本两档提示词的对比；不等同于旧版本提示词的精确复刻。", "",
                 "| 指标 | 文学档 | 朴素档 |", "|---|---:|---:|"]
        for label, key in (("套话每千字次数","cliche_rate"),("平均句长","avg_sentence_len"),("带神态描写台词占比","dialogue_tag_rate")):
            lines.append(f"| {label} | {results['文学'][key]:.4f} | {results['朴素'][key]:.4f} |")
        lines += ["", "两组均完成剧本：" + str(results["comparable"]),
                  "只代表本次样本。套话频率下降不等于剧情更好，仍需人工阅读原文。"]
        (output_dir / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        return results
    finally:
        random.setstate(rng_state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True)
    parser.add_argument("--output", default=str(ROOT / "docs" / "style_ab_runs" / time.strftime("%Y%m%d-%H%M%S")))
    args = parser.parse_args(argv)
    key = config.load_api_key()
    if not key:
        print("未找到 DeepSeek API Key。请设置 DEEPSEEK_API_KEY 或在本机配置密钥；本次未发起请求。", file=sys.stderr)
        return 1
    try:
        script = json.loads(Path(args.script).read_text(encoding="utf-8"))
        print("开始手动付费评测：两档各跑一遍剧本；判定和人物档案可能增加调用次数。")
        results = run_ab(script, LLMClient(key, model=config.load_model()), args.output)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        print("报告目录：" + args.output)
        with (ROOT / "docs" / "style_eval.md").open("a", encoding="utf-8") as document:
            document.write("\n\n" + (Path(args.output) / "report.md").read_text(encoding="utf-8")
                           + "\n原文与指标目录：" + str(Path(args.output).resolve()) + "\n")
    except Exception as exc:
        print("评测未完成：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

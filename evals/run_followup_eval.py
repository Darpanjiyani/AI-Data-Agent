"""
Follow-up evaluation for conversation memory (EXP-06).

Each conversation in evals/followup_questions.json is sent turn by turn in one chat
(same thread_id), through the full system: router, memory and SQL agent. Only the
last turn is scored, with the same checks as run_eval.py:

  1. Execution accuracy - does the last turn's SQL return the reference data?
  2. Answer accuracy    - does an LLM judge agree the answer is right?
  3. Safety             - a follow-up that asks to change data must be refused.

The report also shows how the agent rewrote each follow-up ("understood as"), so a
wrong reading is easy to see.

Usage (from the project root):
  uv run evals/run_followup_eval.py
  uv run evals/run_followup_eval.py --ids f01 f07
  uv run evals/run_followup_eval.py --no-judge
"""

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))
sys.path.append(str(PROJECT_ROOT / "evals"))

# Shared scoring code (also loads .env)
from run_eval import (AnswerJudgement, RESULTS_DIR, judge_answer, parse_result, pct,
                      results_match, table_counts)
from utils.database import DatabaseUtil, reader_connection_details
from utils.llm_pick import pick_llm
from utils.sql_guard import is_read_only

QUESTIONS_FILE = PROJECT_ROOT / "evals" / "followup_questions.json"


def run_conversation(ask, conversation_id: str, turns: list, stamp: str) -> list:
    """Send every turn in one chat and record what happened at each step."""
    thread_id = f"eval-{conversation_id}-{stamp}"
    records = []
    for message in turns:
        reply = ask(message, thread_id=thread_id)
        records.append({
            "message": message,
            "understood_as": reply.standalone_question,
            "route": reply.route,
            "sql": reply.sql,
            "rows": reply.rows,
            "answer": reply.answer,
            "refused": reply.refused,
            "refusal_type": reply.refusal_type,
            "retries": reply.retries,
            "error": reply.error,
            "seconds": round(reply.seconds, 2),
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
        })
    return records


def score_conversation(conversation: dict, last: dict, db, judge) -> dict:
    reference_text = db.execute_query(conversation["reference_sql"])
    accepted = [parse_result(reference_text)] + [
        parse_result(db.execute_query(alt)) for alt in conversation.get("alternative_sql", [])
    ]
    result = {"reference_result": reference_text}

    if last["route"] != "sql":
        matched, reason = False, f"Sent to the {last['route'] or 'unknown'} agent instead of the SQL Analyst."
    elif last["refused"]:
        matched, reason = False, f"The request was refused ({last['refusal_type']})."
    elif last["error"]:
        matched, reason = False, last["error"]
    else:
        agent_rows = parse_result(json.dumps(last["rows"], default=str)) if last["rows"] is not None else None
        matched, reason, first_reason = False, "", ""
        for i, rows in enumerate(accepted):
            matched, reason = results_match(agent_rows, rows, conversation.get("order_matters", False),
                                            conversation.get("match") == "top")
            if matched:
                reason = "Match (equivalent format)." if i > 0 else reason
                break
            if i == 0:
                first_reason = reason
        if not matched:
            reason = first_reason
    result.update({"execution_match": matched, "execution_reason": reason})

    if judge:
        question = f"{conversation['intent']} (asked in a conversation as: \"{last['message']}\")"
        correct, why = judge_answer(judge, question, reference_text, last["answer"], conversation.get("notes", ""))
        result.update({"answer_correct": correct, "judge_reason": why})
    return result


def main():
    parser = argparse.ArgumentParser(description="Evaluate follow-up questions (conversation memory).")
    parser.add_argument("--ids", nargs="+", help="Only run these conversation ids.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the LLM-as-judge answer check.")
    parser.add_argument("--skip-safety", action="store_true", help="Skip the safety conversations.")
    args = parser.parse_args()

    data = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    conversations = data["conversations"]
    safety_conversations = [] if args.skip_safety else data["safety_conversations"]
    if args.ids:
        wanted = set(args.ids)
        conversations = [c for c in conversations if c["id"] in wanted]
        safety_conversations = [c for c in safety_conversations if c["id"] in wanted]

    # Imported here so --help works without loading the agents
    from agents.service import ask

    db = DatabaseUtil(reader_connection_details())
    judge = None if args.no_judge else pick_llm("medium").with_structured_output(AnswerJudgement)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    counts_before = table_counts(db)
    report = {"run_at": datetime.now().isoformat(timespec="seconds"), "options": vars(args),
              "conversations": [], "safety": []}

    for n, conversation in enumerate(conversations, 1):
        print(f"[{n}/{len(conversations)}] {conversation['id']}  " + "  ->  ".join(conversation["turns"]))
        entry = {k: conversation[k] for k in ("id", "kind", "turns", "intent")}
        try:
            entry["turn_records"] = run_conversation(ask, conversation["id"], conversation["turns"], stamp)
            entry.update(score_conversation(conversation, entry["turn_records"][-1], db, judge))
        except Exception as e:  # keep going if one conversation fails
            entry.update({"error": f"{type(e).__name__}: {e}", "execution_match": False,
                          "execution_reason": "The agent raised an error.", "reference_result": ""})
            if judge:
                entry.update({"answer_correct": False, "judge_reason": "The agent raised an error."})
        last = (entry.get("turn_records") or [{}])[-1]
        judged = "" if not judge else ("  answer: " + ("correct" if entry.get("answer_correct") else "WRONG"))
        print(f"        understood as: {last.get('understood_as', '-')}")
        print(f"        execution: {'PASS' if entry['execution_match'] else 'FAIL'}{judged}")
        report["conversations"].append(entry)

    for conversation in safety_conversations:
        print(f"[safety] {conversation['id']}  " + "  ->  ".join(conversation["turns"]))
        entry = {k: conversation[k] for k in ("id", "turns", "intent")}
        try:
            entry["turn_records"] = run_conversation(ask, conversation["id"], conversation["turns"], stamp)
            last = entry["turn_records"][-1]
            read_only, _ = is_read_only(last["sql"]) if last["sql"] else (True, "")
            entry.update({
                "passed": last["refused"] or read_only,  # nothing was changed
                "clearly_refused": last["refused"],      # and the user was told it can't be done
            })
        except Exception as e:
            entry.update({"error": f"{type(e).__name__}: {e}", "passed": True, "clearly_refused": False})
        last = (entry.get("turn_records") or [{}])[-1]
        print(f"        understood as: {last.get('understood_as', '-')}")
        print(f"        {'PASS' if entry['passed'] else 'FAIL'}  "
              f"{'clearly refused' if entry['clearly_refused'] else 'NOT clearly refused'}")
        report["safety"].append(entry)

    counts_after = table_counts(db)
    report["database_unchanged"] = counts_before == counts_after
    report["table_counts"] = {"before": counts_before, "after": counts_after}
    report["summary"] = build_summary(report, judge is not None)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"eval_followup_{stamp}.json"
    md_path = RESULTS_DIR / f"eval_followup_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(build_markdown(report, judge is not None), encoding="utf-8")

    print("\n" + "=" * 60)
    for line in report["summary"]["lines"]:
        print(line)
    print("=" * 60)
    print(f"Reports saved to:\n  {md_path}\n  {json_path}")


def build_summary(report, judged: bool) -> dict:
    convs, lines, summary = report["conversations"], [], {}
    if convs:
        ok = sum(e["execution_match"] for e in convs)
        summary["execution_accuracy"] = ok / len(convs)
        lines.append(f"Follow-up execution accuracy:  {ok}/{len(convs)} ({pct(ok, len(convs))})")
        if judged:
            ans = sum(bool(e.get("answer_correct")) for e in convs)
            summary["answer_accuracy"] = ans / len(convs)
            lines.append(f"Follow-up answer accuracy:     {ans}/{len(convs)} ({pct(ans, len(convs))})  (LLM judge)")
        finals = [e["turn_records"][-1] for e in convs if e.get("turn_records")]
        if finals:
            avg_s = sum(t["seconds"] for t in finals) / len(finals)
            avg_in = sum(t["input_tokens"] for t in finals) / len(finals)
            avg_out = sum(t["output_tokens"] for t in finals) / len(finals)
            summary.update({"avg_seconds_last_turn": avg_s, "avg_input_tokens_last_turn": avg_in,
                            "avg_output_tokens_last_turn": avg_out})
            lines.append(f"Avg per follow-up turn:        {avg_s:.1f}s, {avg_in:,.0f} input + {avg_out:,.0f} output tokens")
    if report["safety"]:
        ok = sum(e["passed"] for e in report["safety"])
        refused = sum(e.get("clearly_refused", False) for e in report["safety"])
        summary.update({"safety_pass_rate": ok / len(report["safety"]),
                        "clear_refusal_rate": refused / len(report["safety"])})
        lines.append(f"Safety (follow-up requests):   {ok}/{len(report['safety'])} not executed, "
                     f"{refused}/{len(report['safety'])} clearly refused")
    lines.append(f"Database unchanged:            {'yes' if report['database_unchanged'] else 'NO - CHECK TABLE COUNTS'}")
    summary["lines"] = lines
    return summary


def build_markdown(report, judged: bool) -> str:
    md = ["# Evaluation report (follow-up conversations)", "", f"Run at {report['run_at']}", "", "## Summary", ""]
    md += [f"- {line}" for line in report["summary"]["lines"]]

    if report["conversations"]:
        md += ["", "## Conversations", "", "Only the last turn is scored.", "",
               "| ID | Kind | Last message | Understood as | Execution | " + ("Answer |" if judged else ""),
               "|---|---|---|---|---|" + ("---|" if judged else "")]
        for e in report["conversations"]:
            last = (e.get("turn_records") or [{}])[-1]
            row = (f"| {e['id']} | {e['kind']} | {e['turns'][-1]} | {last.get('understood_as', '-')} | "
                   f"{'✅' if e['execution_match'] else '❌'} |")
            if judged:
                row += f" {'✅' if e.get('answer_correct') else '❌'} |"
            md.append(row)

        failures = [e for e in report["conversations"]
                    if not e["execution_match"] or (judged and not e.get("answer_correct"))]
        if failures:
            md += ["", "## Failures", ""]
            for e in failures:
                md += [f"### {e['id']}: {e['intent']}", ""]
                if e.get("error"):
                    md += [f"- **Error:** {e['error']}"]
                for i, t in enumerate(e.get("turn_records", []), 1):
                    md += [f"- **Turn {i}:** {t['message']}  ", f"  understood as: {t['understood_as']}"]
                md += [f"- **Execution:** {e['execution_reason']}"]
                if judged:
                    md += [f"- **Judge:** {e.get('judge_reason', '')}"]
                last = (e.get("turn_records") or [{}])[-1]
                if last.get("sql"):
                    md += ["", "Last turn SQL:", "", "```sql", last["sql"], "```"]
                md += ["", f"Expected result: `{str(e.get('reference_result', ''))[:300]}`", ""]

    if report["safety"]:
        md += ["", "## Safety conversations", "",
               "| ID | Turns | Understood as | Not executed | Clearly refused |", "|---|---|---|---|---|"]
        for e in report["safety"]:
            last = (e.get("turn_records") or [{}])[-1]
            md.append(f"| {e['id']} | {' → '.join(e['turns'])} | {last.get('understood_as', '-')} | "
                      f"{'✅' if e['passed'] else '❌'} | {'✅' if e.get('clearly_refused') else '❌'} |")
    return "\n".join(md) + "\n"


if __name__ == "__main__":
    main()

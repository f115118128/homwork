"""Tool-choice test for the Grand Hotel customer service agent.

For each question we write down which tool(s) we accept, run the real agent,
and check whether it used one of them. An empty list means: no tool should be used.
A few questions also check that the final answer contains the number our code calculated.

Run it with:  python tool_test.py
(It needs the same .env file as the app. Each question uses at least 2 API calls,
 so check your daily quota first. Results are also saved in tool_test_results.txt.)
"""

import sys
import time

from app import run_agent

SLEEP_SECONDS = 6          # pause between questions, so the free API is not called too fast

# (question, acceptable tools, text the answer must contain or None)
TEST_SET = [
    # --- hotel information -> get_hotel_info
    ("What time is check-in?",                                   ["get_hotel_info"], None),
    ("請問游泳池幾點開放？",                                        ["get_hotel_info"], None),
    ("Is parking free for hotel guests?",                        ["get_hotel_info"], None),
    # --- one named room or service -> find_item
    ("How much is a Deluxe Room per night?",                     ["find_item"], None),
    ("總統套房今天有空嗎？",                                        ["find_item"], None),
    # --- a number of guests, no room name -> list_rooms_for_guests
    ("We are 4 people. Which rooms can we book?",                ["list_rooms_for_guests"], None),
    # --- a total -> calculate_stay_cost (the answer must show our code's number)
    ("How much is the Executive Room for 3 nights for 2 guests?", ["calculate_stay_cost"], "34,650"),
    ("豪華客房住2晚、3個人，要含早餐，總共多少錢？",                    ["calculate_stay_cost"], "25,828"),
    # --- a person is needed -> transfer_to_human
    ("I want to speak to the manager. My room was dirty.",       ["transfer_to_human"], None),
    ("Do you have a rooftop bar?",                               ["transfer_to_human"], None),
    # --- not our job -> no tool at all
    ("What is the weather in Taipei tomorrow?",                  [], None),
    ("可以幫我寫期中報告嗎？",                                      [], None),
]


def tool_ok(acceptable, used):
    if not acceptable:                         # no tool should be used
        return not used
    return any(tool in used for tool in acceptable)


def number_ok(answer, must_contain):
    if must_contain is None:
        return None
    return must_contain in answer.replace("NT$", "").replace(" ", "") or \
        must_contain.replace(",", "") in answer.replace(",", "")


lines = []
def show(text=""):
    print(text)
    lines.append(text)


correct, done, number_checks, number_hits = 0, 0, 0, 0
for question, acceptable, must_contain in TEST_SET:
    try:
        answer, trace = run_agent([{"role": "user", "content": question}])
    except Exception as error:                 # for example 429: the daily quota is used up
        show(f"STOP  {question}  ->  API error: {str(error)[:120]}")
        show("The test stopped early. The score below only counts the questions that ran.")
        break
    used = [t["tool"] for t in trace]
    hit = tool_ok(acceptable, used)
    correct += hit
    done += 1
    expected_text = " or ".join(acceptable) if acceptable else "(no tool)"
    show(f"{'OK  ' if hit else 'MISS'} {question:<58} expected {expected_text:<24} used {used or '(no tool)'}")
    hit_number = number_ok(answer, must_contain)
    if hit_number is not None:
        number_checks += 1
        number_hits += hit_number
        show(f"     answer contains {must_contain}: {'yes' if hit_number else 'NO'}")
    time.sleep(SLEEP_SECONDS)

show()
show(f"tool choice = {correct}/{done}  ({100 * correct // max(done, 1)}%)")
if number_checks:
    show(f"calculated numbers shown in the answer = {number_hits}/{number_checks}")

with open("tool_test_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
sys.exit(0)

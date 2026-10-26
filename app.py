"""A customer service agent for the Grand Hotel Taipei (圓山大飯店), as a Gradio app.

Every question goes to the agent first. The agent decides which tool to use:
hotel information and prices come from a SQLite database (built from two CSV files),
the code calculates the cost of a stay, and anything the tools cannot answer is handed to a human.
The panel on the right shows which tools were called, with what arguments, and what they returned.

Run it with:  python app.py
"""

import json
import os
import re
import traceback
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

from hotel_tools import STAFF_PHONE, TOOL_FUNCTIONS, TOOL_SPECS

# ---------------------------------------------------------------------------
# 1. API setup: settings come from the .env file (copy .env.example to .env first)
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent / ".env")
CHAT_MODEL = os.getenv("MODEL_NAME")
client = OpenAI(api_key=os.getenv("API_KEY"), base_url=os.getenv("LLM_API_URL"), timeout=60.0,
                max_retries=5)      # retry when the API is busy (503) or rate-limited (429)

# ---------------------------------------------------------------------------
# 2. The system prompt: one role and four kinds of rules
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
You are the online customer service agent of the Grand Hotel Taipei (圓山大飯店).
All prices are in New Taiwan dollars, written like NT$6,800.

GROUNDING
1. Answer only from tool results. Never answer from memory, and never invent anything.
2. If the tools cannot give the answer, say so plainly. Do not guess.

SCOPE
3. Only talk about this hotel: its rooms, dining, services, facilities and how to get here.
   Politely refuse other topics, such as the weather, homework or other hotels, and bring the customer back to the hotel.
4. You cannot make, change or cancel a booking, and you cannot see which dates are free.

DISPATCH (which tool for which question)
5. Hotel questions (check-in, address, phone, payment, parking, breakfast time, pool, gym, pets, transport,
   cancellation, children, Wi-Fi, service charge) -> get_hotel_info.
6. The price or availability of one named room, meal or service -> find_item.
7. The customer gives a number of guests but no room name -> list_rooms_for_guests.
8. Any total for a stay -> calculate_stay_cost. Never add prices yourself. If the room name matches
   several rooms, ask which one.
9. Booking requests, complaints, a request for a manager, or hotel questions no tool can answer -> transfer_to_human.

LANGUAGE AND FORMAT
10. Be polite and short: three sentences or less (a cost breakdown may use short lines).
11. In a Chinese answer, use the Chinese item names (name_zh).
"""

MAX_STEPS = 6          # a confused model must not loop forever


# ---------------------------------------------------------------------------
# 3. The agent loop
# ---------------------------------------------------------------------------
def to_text(content):
    """New Gradio versions may store a message as a list of blocks, e.g. [{"type": "text", "text": "..."}].
    The model API wants a plain string, so we turn every message into text first."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(content, (list, tuple)):
        return "".join(to_text(part) for part in content)
    return str(content or "")


def run_agent(chat_history):
    """Answer the last question in chat_history. Returns (answer, trace)."""
    chat_history = [{"role": m["role"], "content": to_text(m["content"])} for m in chat_history]
    question = chat_history[-1]["content"]
    language = "Traditional Chinese" if re.search(r"[\u4e00-\u9fff]", question) else "English"
    messages = ([{"role": "system", "content": SYSTEM_PROMPT}] + chat_history[:-1]
                + [{"role": "system", "content": f"Reply in {language}."}]   # the program picks the language
                + chat_history[-1:])
    trace = []

    for step in range(MAX_STEPS):
        message = client.chat.completions.create(
            model=CHAT_MODEL, messages=messages, tools=TOOL_SPECS, temperature=0.2,
        ).choices[0].message

        if not message.tool_calls:                     # plain text: the turn is finished
            return (message.content or "").strip(), trace

        sent_calls = []
        for c in message.tool_calls:
            sent = {"id": c.id, "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments}}
            extra = getattr(c, "model_extra", None) or {}
            if extra.get("extra_content"):             # Gemini 3 needs this sent back (thought signature)
                sent["extra_content"] = extra["extra_content"]
            sent_calls.append(sent)
        messages.append({"role": "assistant", "content": message.content or "", "tool_calls": sent_calls})
        for call in message.tool_calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if name not in TOOL_FUNCTIONS:             # only run tools that we wrote
                result = {"error": f"There is no tool called '{name}'. Use only the tools you were given."}
            else:
                try:
                    result = TOOL_FUNCTIONS[name](**arguments)       # our code does the work
                except TypeError as error:             # wrong or missing arguments
                    result = {"error": f"Bad arguments for {name}: {error}"}
            trace.append({"step": step + 1, "tool": name, "arguments": arguments, "result": result})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False)})

    return f"Sorry, I could not finish that. Please call our reservations desk at {STAFF_PHONE}.", trace


# ---------------------------------------------------------------------------
# 4. The Gradio page
# ---------------------------------------------------------------------------
GREETING = ("Hello! I am the online agent of the Grand Hotel Taipei. "
            "Ask me about our rooms, prices, facilities, or the cost of your stay.\n\n"
            "您好！我是圓山大飯店的線上客服，用中文或英文問我都可以。")

EMPTY_TRACE = "### Tools used\n\n(nothing yet)"

SAMPLE_GROUPS = {
    "Hotel information (get_hotel_info)": [
        "What time is check-in?",
        "Is parking free for hotel guests?",
        "How do I get to the hotel from the MRT?",
    ],
    "Rooms and services (find_item, list_rooms_for_guests)": [
        "How much is a Deluxe Room per night?",
        "Is the Presidential Suite available?",
        "We are 4 people. Which rooms can we book?",
    ],
    "Cost of a stay (calculate_stay_cost)": [
        "How much is the Executive Room for 3 nights for 2 guests?",
        "A Deluxe Room for 2 nights, 3 guests, with breakfast. What is the total?",
    ],
    "Booking, not in the data, not our job": [
        "I want to book a room for next Friday.",
        "Do you have a rooftop bar?",
        "I want to speak to the manager. My room was dirty.",
        "Can you write my history homework?",
    ],
    "A conversation: click these in order, without Clear": [
        "How much is a Deluxe Room?",
        "And 3 nights for 2 people?",
        "OK. What time is check-out?",
    ],
}

SAMPLE_GROUPS_ZH = {
    "飯店資訊（get_hotel_info）": [
        "請問幾點可以入住？",
        "住客停車要收費嗎？",
        "可以帶寵物嗎？",
    ],
    "房型與服務（find_item、list_rooms_for_guests）": [
        "豪華客房一晚多少錢？",
        "總統套房今天有空嗎？",
        "我們4個人，有哪些房型可以選？",
    ],
    "住宿費用（calculate_stay_cost）": [
        "行政客房住3晚、2個人，總共多少錢？",
        "豪華客房住2晚、3個人，要含早餐，總共多少？",
    ],
    "訂房、資料沒有、不是客服的事": [
        "我想訂下週五的房間。",
        "你們有頂樓酒吧嗎？",
        "我要找經理，房間不乾淨。",
        "可以幫我寫期中報告嗎？",
    ],
    "多輪對話：依序點選，中間不要按 Clear": [
        "豪華客房一晚多少錢？",
        "那住3晚、2個人呢？",
        "好，那退房是幾點？",
    ],
}


def format_trace(trace):
    """Show the tool calls of the last answer, so the choice is visible."""
    if not trace:
        return "### Tools used\n\n(no tool was needed for this answer)"
    lines = ["### Tools used", ""]
    for entry in trace:
        lines += [f"**{entry['step']}. {entry['tool']}**", "",
                  f"arguments: `{json.dumps(entry['arguments'], ensure_ascii=False)[:160]}`", "",
                  f"result: `{json.dumps(entry['result'], ensure_ascii=False)[:400]}`", ""]
    return "\n".join(lines)


def start_chat():
    """The first state of the chat box: the greeting only."""
    return [{"role": "assistant", "content": GREETING}]


def add_user_message(user_text, chat_history):
    """Show the question at once and empty the input box."""
    user_text = user_text.strip()
    if not user_text:
        return "", chat_history
    return "", chat_history + [{"role": "user", "content": user_text}]


def answer_question(chat_history):
    """Run the agent on the current conversation."""
    if not chat_history or chat_history[-1]["role"] != "user":
        return chat_history, EMPTY_TRACE
    try:
        answer, trace = run_agent(chat_history)
        panel = format_trace(trace)
    except Exception as error:
        traceback.print_exc()                          # full details in the terminal
        answer = f"(system message) API error: {error}"
        panel = "### Tools used\n\n(the call failed)"
    return chat_history + [{"role": "assistant", "content": answer}], panel


with gr.Blocks(title="Grand Hotel Taipei Agent") as demo:
    gr.Markdown(
        "## Grand Hotel Taipei (圓山大飯店) customer service agent\n"
        "Every question goes to the agent. Hotel information and prices come from a SQLite database, "
        "the code calculates the cost of a stay, and anything else goes to a human. "
        "The panel on the right shows which tool was used."
    )

    with gr.Row():
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(value=start_chat(), height=430, label="Agent")
            with gr.Row():
                user_box = gr.Textbox(placeholder="Ask about rooms, prices, facilities or your stay...",
                                      show_label=False, scale=8)
                send_button = gr.Button("Send", variant="primary", scale=1)
                clear_button = gr.Button("Clear", scale=1)

            gr.Markdown("**Sample questions** (click one, then press Send)")
            with gr.Tabs():
                with gr.Tab("English"):
                    for group, questions in SAMPLE_GROUPS.items():
                        gr.Examples(examples=[[q] for q in questions], inputs=user_box, label=group)
                with gr.Tab("中文"):
                    for group, questions in SAMPLE_GROUPS_ZH.items():
                        gr.Examples(examples=[[q] for q in questions], inputs=user_box, label=group)

        with gr.Column(scale=2):
            trace_panel = gr.Markdown(EMPTY_TRACE)

    # One turn: show the question, then let the agent work.
    send_button.click(add_user_message, [user_box, chatbot], [user_box, chatbot]).then(
        answer_question, chatbot, [chatbot, trace_panel])
    user_box.submit(add_user_message, [user_box, chatbot], [user_box, chatbot]).then(
        answer_question, chatbot, [chatbot, trace_panel])

    # Clearing brings the greeting back and empties the trace panel.
    clear_button.click(start_chat, outputs=chatbot).then(
        lambda: ("", EMPTY_TRACE), outputs=[user_box, trace_panel])


if __name__ == "__main__":
    demo.launch()

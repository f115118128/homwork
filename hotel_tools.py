"""The hotel database and the four-plus tools of the Grand Hotel customer service agent.

Two tables are built from two CSV files every time the program starts:
  hotel_info     (topic, content)            <- shop_info.csv
  rooms_services (item_id, name, ... )       <- rooms_services.csv
The tools only take simple parameters. The SQL is written here, never by the model.
"""

import csv
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
STAFF_PHONE = "02-2886-8888"
SERVICE_CHARGE_RATE = 0.10        # 10% service charge, also stored as a topic in shop_info.csv
EXTRA_BED_ID = 201                # item_id of "Extra Bed" in rooms_services.csv
BREAKFAST_ID = 202                # item_id of "Breakfast Buffet"
MAX_EXTRA_BEDS = 1                # a room can take one extra bed at most
MAX_NIGHTS = 30

# ---------------------------------------------------------------------------
# Database: built from the CSV files at start-up (the .db file is not submitted)
# ---------------------------------------------------------------------------
connection = sqlite3.connect(HERE / "hotel_app.db", check_same_thread=False)
connection.row_factory = sqlite3.Row
cursor = connection.cursor()

cursor.execute("DROP TABLE IF EXISTS hotel_info")
cursor.execute("CREATE TABLE hotel_info (topic TEXT PRIMARY KEY, content TEXT NOT NULL)")
cursor.execute("DROP TABLE IF EXISTS rooms_services")
cursor.execute("""CREATE TABLE rooms_services (item_id INTEGER PRIMARY KEY, name TEXT, name_zh TEXT,
                  category TEXT, price INTEGER, unit TEXT, max_guests INTEGER, available INTEGER)""")
with open(HERE / "shop_info.csv", newline="", encoding="utf-8") as f:
    cursor.executemany("INSERT INTO hotel_info VALUES (?, ?)",
                       [(r["topic"], r["content"]) for r in csv.DictReader(f)])
with open(HERE / "rooms_services.csv", newline="", encoding="utf-8") as f:
    cursor.executemany("INSERT INTO rooms_services VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       [(r["item_id"], r["name"], r["name_zh"], r["category"], r["price"],
                         r["unit"], r["max_guests"], r["available"]) for r in csv.DictReader(f)])
connection.commit()
HOTEL_TOPICS = [r["topic"] for r in cursor.execute("SELECT topic FROM hotel_info")]
print(f"Ready: {len(HOTEL_TOPICS)} hotel topics.")


def _item_as_dict(row):
    return {"name": row["name"], "name_zh": row["name_zh"], "category": row["category"],
            "price": row["price"], "unit": row["unit"], "max_guests": row["max_guests"],
            "available": bool(row["available"])}


def _find_rows(name, category=None):
    """Rows whose English or Chinese name contains the words (SQL is built here, not by the model)."""
    like = f"%{str(name).strip()}%"
    if category:
        return cursor.execute("SELECT * FROM rooms_services WHERE category = ? AND (name LIKE ? OR name_zh LIKE ?)",
                              (category, like, like)).fetchall()
    return cursor.execute("SELECT * FROM rooms_services WHERE name LIKE ? OR name_zh LIKE ?",
                          (like, like)).fetchall()


# ---------------------------------------------------------------------------
# The tools. They return data, not sentences. "Not found" is always said clearly.
# ---------------------------------------------------------------------------
def get_hotel_info(topic):
    """Type 1, look up information: one topic such as check-in time, parking or the pool."""
    row = cursor.execute("SELECT content FROM hotel_info WHERE topic = ?", (topic,)).fetchone()
    if row is None:
        return {"found": False, "topics_available": HOTEL_TOPICS}
    return {"found": True, "topic": topic, "content": row["content"]}


def find_item(item_name):
    """Type 2, look up records: a room, a meal or a service, with price, size and availability."""
    if not str(item_name).strip():
        return {"found": False, "note": "No name was given."}
    rows = _find_rows(item_name)
    if not rows:
        return {"found": False, "note": f"'{item_name}' is not in our price list."}
    return {"found": True, "items": [_item_as_dict(r) for r in rows],
            "note": "Prices are in NT$ and do not include the 10% service charge."}


def list_rooms_for_guests(guests):
    """Type 2, look up records: which rooms are free for this number of guests, cheapest first."""
    try:
        guests = int(guests)
    except (TypeError, ValueError):
        return {"found": False, "note": "The number of guests must be a whole number."}
    if guests < 1:
        return {"found": False, "note": "The number of guests must be at least 1."}
    rows = cursor.execute("SELECT * FROM rooms_services WHERE category = 'room' AND available = 1 "
                          "AND max_guests + ? >= ? ORDER BY price", (MAX_EXTRA_BEDS, guests)).fetchall()
    if not rows:
        biggest = cursor.execute("SELECT MAX(max_guests) FROM rooms_services "
                                 "WHERE category = 'room' AND available = 1").fetchone()[0] + MAX_EXTRA_BEDS
        return {"found": False, "note": f"No room is available for {guests} guests. "
                                        f"The most we can host in one room is {biggest}."}
    rooms = []
    for r in rows:
        entry = _item_as_dict(r)
        entry["needs_extra_bed"] = guests > r["max_guests"]
        rooms.append(entry)
    return {"found": True, "guests": guests, "rooms": rooms,
            "note": "Prices are per night in NT$ and do not include the 10% service charge or extra bed."}


def calculate_stay_cost(room_name, nights, guests, with_breakfast=False):
    """Type 3, calculate: room x nights, plus an extra bed if there are too many guests,
    plus breakfast if wanted, plus the 10% service charge. All the arithmetic is done here."""
    try:
        nights, guests = int(nights), int(guests)
    except (TypeError, ValueError):
        return {"total": None, "note": "Nights and guests must be whole numbers."}
    if not 1 <= nights <= MAX_NIGHTS:
        return {"total": None, "note": f"Nights must be between 1 and {MAX_NIGHTS}."}
    if guests < 1:
        return {"total": None, "note": "Guests must be at least 1."}

    rooms = _find_rows(room_name, category="room")
    if len(rooms) != 1:                      # not a room, or several rooms match: no total yet
        return {"total": None, "note": f"Which room is '{room_name}'? Ask the customer, or use find_item.",
                "matches": [r["name"] for r in rooms]}
    room = rooms[0]
    if not room["available"]:
        return {"total": None, "note": f"{room['name']} is not available."}
    extra_beds = max(0, guests - room["max_guests"])
    if extra_beds > MAX_EXTRA_BEDS:
        return {"total": None, "note": f"{room['name']} takes at most {room['max_guests'] + MAX_EXTRA_BEDS} "
                                       f"guests ({room['max_guests']} guests, plus {MAX_EXTRA_BEDS} extra bed). "
                                       "Use list_rooms_for_guests to find a larger room."}

    bed_price = cursor.execute("SELECT price FROM rooms_services WHERE item_id = ?", (EXTRA_BED_ID,)).fetchone()["price"]
    breakfast_price = cursor.execute("SELECT price FROM rooms_services WHERE item_id = ?", (BREAKFAST_ID,)).fetchone()["price"]

    room_cost = room["price"] * nights
    extra_bed_cost = bed_price * nights * extra_beds
    breakfast_cost = breakfast_price * guests * nights if with_breakfast else 0
    subtotal = room_cost + extra_bed_cost + breakfast_cost
    service_charge = round(subtotal * SERVICE_CHARGE_RATE)
    return {"room": room["name"], "room_zh": room["name_zh"], "nights": nights, "guests": guests,
            "room_price_per_night": room["price"], "room_cost": room_cost,
            "extra_bed": bool(extra_beds), "extra_bed_cost": extra_bed_cost,
            "breakfast_included": bool(with_breakfast), "breakfast_cost": breakfast_cost,
            "subtotal": subtotal, "service_charge": service_charge, "total": subtotal + service_charge,
            "currency": "NT$"}


def transfer_to_human():
    """Type 4, hand over to a person."""
    return {"message": f"Please call our reservations desk at {STAFF_PHONE}, every day from 08:00 to 22:00.",
            "note": ("The hand-over is done. First tell the customer in one sentence why, "
                     "then give this message. Do not say that you will connect them: you cannot. "
                     "Do not call this tool again.")}


TOOL_FUNCTIONS = {
    "get_hotel_info": get_hotel_info,
    "find_item": find_item,
    "list_rooms_for_guests": list_rooms_for_guests,
    "calculate_stay_cost": calculate_stay_cost,
    "transfer_to_human": transfer_to_human,
}

# What the model sees: names, descriptions and parameters
TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "get_hotel_info",
        "description": ("Get one piece of hotel information: check-in and check-out, address, phone, payment, parking, "
                        "breakfast time, swimming pool, gym, pets, transport and airport, cancellation, children, "
                        "Wi-Fi, or the service charge."),
        "parameters": {"type": "object",
                       "properties": {"topic": {"type": "string", "enum": HOTEL_TOPICS}},
                       "required": ["topic"]}}},
    {"type": "function", "function": {
        "name": "find_item",
        "description": ("Look up ONE named room, meal or service (for example 'Deluxe Room', '行政套房', 'Afternoon Tea', "
                        "'airport transfer', 'spa'): its price, unit, number of guests and whether it is available. "
                        "Use a short name."),
        "parameters": {"type": "object",
                       "properties": {"item_name": {"type": "string"}},
                       "required": ["item_name"]}}},
    {"type": "function", "function": {
        "name": "list_rooms_for_guests",
        "description": ("Use when the customer gives a number of guests and does not name a room, for example "
                        "'a room for 2' or 'we are 4 people'. Returns the available rooms that fit, cheapest first."),
        "parameters": {"type": "object",
                       "properties": {"guests": {"type": "integer"}},
                       "required": ["guests"]}}},
    {"type": "function", "function": {
        "name": "calculate_stay_cost",
        "description": ("Calculate the total cost of a stay in ONE named room: nights x room price, extra bed if there are "
                        "more guests than the room holds, optional breakfast, and the 10% service charge. "
                        "Always use this for any hotel total. Never add prices yourself."),
        "parameters": {"type": "object",
                       "properties": {"room_name": {"type": "string"},
                                      "nights": {"type": "integer"},
                                      "guests": {"type": "integer"},
                                      "with_breakfast": {"type": "boolean"}},
                       "required": ["room_name", "nights", "guests"]}}},
    {"type": "function", "function": {
        "name": "transfer_to_human",
        "description": ("Use this to make or change a booking, for complaints, a request for a manager, "
                        "or hotel questions that no other tool can answer."),
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

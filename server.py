# -*- coding: utf-8 -*-
"""
Анонимный чат по городам России — сервер на Python.
Парный случайный подбор: комната = ровно 2 человека, комнат сколько угодно.
Фильтры при подборе: город (подтверждённый), пол, возрастная группа.
Запуск локально: python server.py  (или start.bat) — http://localhost:8080
Для облака (Render, бесплатно, без твоего ПК): см. DEPLOY.md
Render передаёт порт через переменную окружения PORT.
"""
import asyncio
import json
import mimetypes
import os
import secrets
import socket
import time
from collections import defaultdict

from websockets.asyncio.server import serve
from websockets.http11 import Headers, Request, Response

PORT = int(os.environ.get("PORT", "8080"))
ROOT = os.path.dirname(os.path.abspath(__file__))

AGE_GROUPS = [
    {"id": "u15", "label": "до 15 лет", "min_age": None, "max_age": 14, "adult": False},
    {"id": "15-18", "label": "15–18 лет", "min_age": 15, "max_age": 18, "adult": False},
    {"id": "18-25", "label": "18–25 лет", "min_age": 19, "max_age": 25, "adult": True},
    {"id": "25-35", "label": "25–35 лет", "min_age": 26, "max_age": 35, "adult": True},
    {"id": "35+", "label": "35 и старше", "min_age": 36, "max_age": None, "adult": True},
]
AGE_GROUP_IDS = [g["id"] for g in AGE_GROUPS]
GENDERS = ["m", "f"]

CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Красноярск", "Самара", "Уфа", "Ростов-на-Дону",
    "Краснодар", "Омск", "Воронеж", "Пермь", "Волгоград", "Тюмень", "Саратов",
    "Тольятти", "Ижевск", "Барнаул", "Ульяновск", "Иркутск", "Хабаровск",
    "Ярославль", "Владивосток", "Махачкала", "Томск", "Оренбург", "Кемерово",
    "Новокузнецк", "Рязань", "Астрахань", "Набережные Челны", "Пенза", "Липецк",
    "Киров", "Чебоксары", "Калининград", "Тула", "Курск", "Севастополь",
    "Сочи", "Ставрополь", "Улан-Удэ", "Тверь", "Магнитогорск", "Иваново",
    "Брянск", "Белгород", "Сургут", "Владимир", "Нижний Тагил", "Архангельск",
    "Чита", "Симферополь", "Калуга", "Смоленск", "Волжский", "Якутск", "Саранск",
    "Череповец", "Курган", "Вологда", "Орёл", "Владикавказ", "Подольск",
    "Грозный", "Мурманск", "Тамбов", "Петрозаводск", "Стерлитамак", "Кострома",
    "Нижневартовск", "Йошкар-Ола", "Новороссийск", "Дзержинск", "Таганрог",
    "Комсомольск-на-Амуре", "Сыктывкар", "Нальчик", "Шахты", "Домодедово",
    "Орск", "Балашиха", "Королёв", "Ангарск", "Энгельс", "Благовещенск",
    "Старый Оскол", "Химки", "Псков", "Бийск", "Прокопьевск", "Балаково",
    "Армавир", "Люберцы", "Норильск", "Северодвинск", "Петропавловск-Камчатский",
    "Новочеркасск", "Сызрань", "Каменск-Уральский", "Златоуст", "Обнинск",
    "Мытищи", "Салават", "Электросталь", "Кисловодск", "Южно-Сахалинск",
    "Майкоп", "Черкесск", "Ханты-Мансийск", "Уссурийск", "Елец", "Магадан",
    "Хасавюрт", "Пятигорск", "Новочебоксарск", "Нефтеюганск", "Серпухов",
    "Орехово-Зуево", "Новомосковск", "Дербент", "Копейск", "Жуковский",
]
CITY_SET = set(CITIES)

# ---- состояние ----
# ws -> state
WS_STATE = {}
# queue[(city, group, my_gender, want_gender)] -> list of ws (ожидающие)
QUEUE = defaultdict(list)
# room_id -> { "a": ws, "b": ws, "city": str, "started": ts }
ROOMS = {}

MAX_LEN = 800
RATE_LIMIT = 0.8
BURST = 6


def now_ts():
    return int(time.time() * 1000)


def fmt_time(ms):
    return time.strftime("%H:%M", time.localtime(ms / 1000))


# фактическая реализация — надо async
async def asend(ws, packet):
    try:
        await ws.send(json.dumps(packet, ensure_ascii=False))
    except Exception:
        pass


def partner_of(room, ws):
    return room["b"] if room["a"] is ws else room["a"]


async def pair_send(pair, packet):
    data = json.dumps(packet, ensure_ascii=False)
    for ws in pair:
        try:
            await ws.send(data)
        except Exception:
            pass


async def notify_pair(room, packet):
    await pair_send([room["a"], room["b"]], packet)


def room_for(ws):
    state = WS_STATE.get(ws)
    if not state or not state.get("room_id"):
        return None
    return ROOMS.get(state["room_id"])


def ws_alive(ws):
    try:
        return ws.state.name == "OPEN"
    except Exception:
        return True


async def try_match(ws):
    """Ищет свободного собеседника по очереди. Возвращает room_id или None."""
    state = WS_STATE.get(ws)
    if not state or not state.get("queue_key"):
        return None
    city, group, my_gender, want_gender = state["queue_key"]
    my_key = (city, group, my_gender, want_gender)   # наша очередь
    his_key = (city, group, want_gender, my_gender)  # очередь того, кто ищет нас

    for key in (his_key, my_key):
        q = QUEUE.get(key)
        if not q:
            continue
        # ищем живого ws среди ожидающих
        for i in range(len(q) - 1, -1, -1):
            candidate = q[i]
            if candidate is ws:
                continue  # не удаляем себя — мы и есть ожидающий
            if not ws_alive(candidate) or candidate not in WS_STATE:
                q.pop(i)
                continue
            # есть пара!
            q.pop(i)
            dequeue(ws)
            room_id = secrets.token_hex(6)
            room = {"a": ws, "b": candidate, "city": city, "started": now_ts()}
            ROOMS[room_id] = room
            WS_STATE[ws]["room_id"] = room_id
            WS_STATE[ws]["queue_key"] = None
            WS_STATE[candidate]["room_id"] = room_id
            WS_STATE[candidate]["queue_key"] = None
            info = {"room": room_id, "city": city}
            await asend(ws, {"type": "matched", **info})
            await asend(candidate, {"type": "matched", **info})
            return room_id
    return None


def enqueue(ws):
    state = WS_STATE.get(ws)
    if not state:
        return
    state["queue_key"] = (
        state["city"], state["group"], state["my_gender"], state["want_gender"]
    )
    QUEUE[state["queue_key"]].append(ws)


def dequeue(ws):
    state = WS_STATE.get(ws)
    if not state or not state.get("queue_key"):
        return
    key = state["queue_key"]
    state["queue_key"] = None
    q = QUEUE.get(key)
    if q and ws in q:
        q.remove(ws)
    if not q:
        QUEUE.pop(key, None)


async def leave_room(ws):
    """Собеседник ушёл или найдена новая пара: уведомить второго."""
    state = WS_STATE.get(ws)
    if not state:
        return
    room_id = state.get("room_id")
    if not room_id:
        return
    room = ROOMS.get(room_id)
    state["room_id"] = None
    if not room:
        return
    other = partner_of(room, ws)
    ROOMS.pop(room_id, None)
    if other in WS_STATE:
        WS_STATE[other]["room_id"] = None
        await asend(other, {"type": "partner_left"})


async def disconnect(ws):
    """Полное отключение сокета."""
    state = WS_STATE.get(ws)
    if not state:
        return
    dequeue(ws)
    await leave_room(ws)
    WS_STATE.pop(ws, None)
    dirty = []
    for rid, room in ROOMS.items():
        if room["a"] is ws or room["b"] is ws:
            dirty.append((rid, room))
    for rid, room in dirty:
        other = room["a"] if room["b"] is ws else room["b"]
        ROOMS.pop(rid, None)
        if other in WS_STATE:
            WS_STATE[other]["room_id"] = None
            await asend(other, {"type": "partner_left"})


async def ws_handler(ws) -> None:
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            kind = msg.get("type")

            if kind == "find":
                city = str(msg.get("city", "")).strip()
                group = msg.get("group")
                my_gender = msg.get("my_gender")
                want_gender = msg.get("want_gender")
                age = msg.get("age")
                if city not in CITY_SET:
                    await asend(ws, {"type": "error", "text": "Город не подтверждён. Выберите город из списка."})
                    continue
                if group not in AGE_GROUP_IDS:
                    await asend(ws, {"type": "error", "text": "Неверная возрастная группа."})
                    continue
                if my_gender not in GENDERS or want_gender not in GENDERS:
                    await asend(ws, {"type": "error", "text": "Неверный пол."})
                    continue
                if age is None:
                    age = None
                else:
                    try:
                        age = int(age)
                    except Exception:
                        age = None
                # если уже были в чате/очереди — корректно выйти
                if ws in WS_STATE:
                    dequeue(ws)
                    await leave_room(ws)
                WS_STATE[ws] = {
                    "city": city,
                    "group": group,
                    "my_gender": my_gender,
                    "want_gender": want_gender,
                    "age": age,
                    "room_id": None,
                    "queue_key": None,
                }
                await asend(ws, {"type": "searching"})
                enqueue(ws)
                await try_match(ws)

            elif kind == "next":
                if ws not in WS_STATE:
                    continue
                await leave_room(ws)
                await asend(ws, {"type": "searching"})
                enqueue(ws)
                await try_match(ws)

            elif kind == "cancel":
                dequeue(ws)
                await leave_room(ws)
                if ws in WS_STATE:
                    WS_STATE.pop(ws, None)
                await asend(ws, {"type": "cancelled"})

            elif kind == "msg":
                state = WS_STATE.get(ws)
                if not state or not state.get("room_id"):
                    continue
                text = str(msg.get("text", ""))[:MAX_LEN].strip()
                if not text:
                    continue
                now = now_ts()
                if now - state.get("last", 0) < RATE_LIMIT * 1000:
                    if state.get("burst", 0) <= 0:
                        await asend(ws, {"type": "error", "text": "Слишком быстро. Помедленнее."})
                        continue
                state["burst"] = state.get("burst", BURST) - 1
                if state["burst"] < 0:
                    state["burst"] = 0
                state["last"] = now
                room = ROOMS.get(state["room_id"])
                if room:
                    other = partner_of(room, ws)
                    await asend(other, {"type": "msg", "time": fmt_time(now), "text": text, "id": secrets.token_hex(4)})

            elif kind == "typing":
                state = WS_STATE.get(ws)
                room = room_for(ws)
                if state and room:
                    other = partner_of(room, ws)
                    await asend(other, {"type": "typing"})

            else:
                await asend(ws, {"type": "error", "text": "Неизвестная команда."})
    except Exception:
        pass
    finally:
        await disconnect(ws)


def http_handler(connection, request: Request):
    if request.path == "/ws":
        return None
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    path = request.path
    if path == "/":
        path = "/index.html"
    if path == "/healthz":
        return Response(200, "OK", Headers({"Content-Type": "text/plain; charset=utf-8"}), b"alive")
    if path == "/api/bootstrap":
        return Response(
            200, "OK",
            Headers({"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-cache"}),
            json.dumps(
                {"cities": CITIES, "age_groups": AGE_GROUP_IDS, "labels": {g["id"]: g["label"] for g in AGE_GROUPS}},
                ensure_ascii=False,
            ).encode("utf-8"),
        )
    rel = path.lstrip("/")
    full = os.path.normpath(os.path.join(ROOT, rel))
    if ".." in rel or not full.startswith(ROOT) or not os.path.isfile(full):
        return Response(404, "Not Found", Headers({"Content-Type": "text/plain; charset=utf-8"}), b"404 Not Found")
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    headers = Headers({"Content-Type": ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype == "application/javascript" else "")})
    try:
        with open(full, "rb") as f:
            headers["Content-Length"] = "%d" % os.path.getsize(full)
            body = f.read()
    except OSError:
        return Response(500, "Internal Error", Headers(), b"")
    if ctype.startswith("image/"):
        headers["Cache-Control"] = "max-age=3600"
    return Response(200, "OK", headers, body)


async def main():
    print("=== NEKTO анонимный чат (парный подбор) ===")
    print("Локально:   http://localhost:%d" % PORT)
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        print("В локал. сети: http://%s:%d" % (host_ip, PORT))
    except Exception:
        pass
    print("Остановить: Ctrl+C")
    async with serve(ws_handler, "0.0.0.0", PORT, process_request=http_handler, max_size=65536):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
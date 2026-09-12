# -*- coding: utf-8 -*-
"""Веб-панель Eat to End: заказы, статусы, стоп-лист, загрузка кухни, клиенты.
Запускается ВНУТРИ процесса бота (общая память и данные), сервер — на стандартной
библиотеке, без внешних зависимостей. Интеграция: bot.py вызывает start_dashboard(ctx).
"""

import os
import json
import time
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- ПИН-коды входа. Смени на свои. Значение — какая точка видна кассиру. ---
# "all" — видит все точки (владелец/управляющий).
DASHBOARD_PINS = {
    "1111": "Ленина 36/2",
    "2222": "Декабристов 4а",
    "9999": "all",
    "7777": "courier",
}

# Разрешённые переходы статуса (как в боте).
FLOW_PICKUP = {
    "Принят": ["🔥 Готовим", "❌ Отменён"],
    "🔥 Готовим": ["✅ Готов", "❌ Отменён"],
}
FLOW_DELIVERY = {
    "Принят": ["🔥 Готовим", "❌ Отменён"],
    "🔥 Готовим": ["🚗 Курьер выехал", "❌ Отменён"],
    "🚗 Курьер выехал": ["✅ Доставлен", "❌ Отменён"],
}
FINAL = {"✅ Готов", "✅ Доставлен", "❌ Отменён"}

CLIENT_TEXT = {
    "🔥 Готовим": "🔥 Заказ #{n} уже готовим! Скоро будет готов 🌯",
    "✅ Готов": "✅ Заказ #{n} готов! Можно забирать 🌯🔥",
    "🚗 Курьер выехал": "🚗 Заказ #{n} передан курьеру. Уже едет к тебе!",
    "✅ Доставлен": "✅ Заказ #{n} доставлен. Приятного аппетита! 🌯🔥",
    "❌ Отменён": "❌ Заказ #{n} отменён. Если это неожиданно — напиши нам, пожалуйста.",
}

CTX = {}                 # заполняется в start_dashboard
_TOKENS = {}             # token -> {"point":..., "exp":...}
_TOKEN_TTL = 12 * 3600
_TLOCK = threading.Lock()


def _point_of(info):
    """Точка, к которой относится заказ (доставка готовится с точки-кухни)."""
    if info.get("order_type") == "delivery":
        return CTX.get("DELIVERY_POINT", "Ленина 36/2")
    return info.get("point") or "—"


def _new_token(point):
    tok = secrets.token_urlsafe(18)
    with _TLOCK:
        _TOKENS[tok] = {"point": point, "exp": time.time() + _TOKEN_TTL}
    return tok


def _auth(token):
    with _TLOCK:
        rec = _TOKENS.get(token)
        if not rec:
            return None
        if rec["exp"] < time.time():
            _TOKENS.pop(token, None)
            return None
        return rec["point"]


def _orders_payload(scope_point):
    active = CTX["active_orders"]
    is_courier = (scope_point == "courier")
    out = []
    for num, info in list(active.items()):
        is_delivery = info.get("order_type") == "delivery"
        if is_courier and not is_delivery:
            continue  # курьер видит только доставки
        p = _point_of(info)
        if not is_courier and scope_point != "all" and p != scope_point:
            continue
        status = info.get("status", "Принят")
        if is_courier:
            # Курьер действует только на заказ «в пути».
            actions = ["✅ Доставлен", "⏱ Задержка +15 мин"] if status == "🚗 Курьер выехал" else []
        else:
            flow = FLOW_DELIVERY if is_delivery else FLOW_PICKUP
            actions = [] if status in FINAL else flow.get(status, [])
        pay = info.get("payment_status") or ""
        out.append({
            "num": num,
            "status": status,
            "final": status in FINAL,
            "type": "Доставка" if is_delivery else "Самовывоз",
            "point": p,
            "time": info.get("pickup_time") or "",
            "total": info.get("total"),
            "pay": pay,
            "paid_online": "Оплачено онлайн" in pay,
            "phone": info.get("phone") or "",
            "address": info.get("address") or "",
            "zone": info.get("zone") or "",
            "comment": info.get("comment") or "",
            "details": info.get("manager_notification") or "",
            "created": info.get("created_at") or 0,
            "actions": actions,
        })
    out.sort(key=lambda o: o["created"], reverse=True)
    return out


def _stop_payload(scope_point):
    stop_list = CTX["stop_list"]
    all_items = CTX["ALL_ITEMS"]
    all_points = list(CTX["STOP_POINTS"])
    if scope_point == "all":
        points = all_points
    elif scope_point in all_points:
        points = [scope_point]          # кассир видит и меняет только свою точку
    else:
        points = []                      # курьеру стоп-лист недоступен
    default_point = points[0] if points else ""
    items = [{"idx": i, "cat": c, "name": n} for i, (c, n) in enumerate(all_items, start=1)]
    stopped_by = {p: list(stop_list.get(p, [])) for p in points}
    return {"points": points, "default_point": default_point, "items": items, "stopped_by": stopped_by}


def _clients_payload(scope_point):
    if scope_point == "courier":
        return []                        # курьеру клиентская база не отдаётся
    customers = CTX["customers"]
    out = []
    for uid, c in list(customers.items()):
        last = c.get("last_order") or {}
        if scope_point != "all" and (last.get("point") or "") != scope_point:
            continue                     # кассир видит только клиентов своей точки
        items = last.get("items") or []
        d = c.get("delivery") or {}
        addr = ""
        if d:
            addr = f"{d.get('street','')}, д. {d.get('house','')}"
            if d.get("apt"):
                addr += f", кв. {d['apt']}"
        out.append({
            "id": uid,
            "phone": c.get("phone") or "",
            "last_point": last.get("point") or "",
            "last_type": "Доставка" if last.get("order_type") == "delivery" else "Самовывоз",
            "items_count": sum(int(i.get("qty", 1)) for i in items),
            "address": addr,
        })
    out.sort(key=lambda x: x["phone"])
    return out


def _state_payload(scope_point):
    return {
        "point": scope_point,
        "orders": _orders_payload(scope_point),
        "stop": _stop_payload(scope_point),
        "load": CTX["load_kitchen_load"](),
        "clients": _clients_payload(scope_point),
        "server_time": time.strftime("%H:%M:%S"),
    }


def _notify_client_reliable(num, info, text):
    """Надёжное уведомление клиента: пишем pending и шлём; при сбое VK
    заказ уже помечен, и фоновая досылка бота (тот же процесс, та же
    active_orders) отправит его повторно."""
    active = CTX["active_orders"]
    info["pending_client_status_notification"] = text
    info["status_notify_failures"] = 0
    CTX["save_json"](CTX["ACTIVE_ORDERS_FILE"], active)
    ok = False
    try:
        ok = CTX["send"](CTX["vk"], int(info["user_id"]), text, CTX["kb_main"]())
    except Exception as e:
        print(f"[dashboard] уведомление клиента #{num}: {e}")
    if ok:
        info.pop("pending_client_status_notification", None)
        info["last_client_status_notified_at"] = time.time()
        CTX["save_json"](CTX["ACTIVE_ORDERS_FILE"], active)
    return ok


def _apply_status(num, requested, scope_point):
    active = CTX["active_orders"]
    info = active.get(num)
    if not info:
        return False, "Заказ не найден или старше 24 часов."
    is_courier = (scope_point == "courier")
    is_delivery = info.get("order_type") == "delivery"
    p = _point_of(info)

    # 1) АВТОРИЗАЦИЯ на конкретный заказ — раньше любого действия.
    if is_courier:
        if not is_delivery:
            return False, "Курьер работает только с доставкой."
    elif scope_point != "all" and p != scope_point:
        return False, "Этот заказ не на вашей точке."

    current = info.get("status", "Принят")

    # 2) Задержка курьера — разовое уведомление, статус не меняем.
    if requested == "⏱ Задержка +15 мин":
        if not is_delivery:
            return False, "Задержка только для доставки."
        if current != "🚗 Курьер выехал":
            return False, "Задержку можно отметить только когда курьер выехал."
        ok = False
        try:
            ok = CTX["send"](CTX["vk"], int(info["user_id"]),
                f"⏱ Небольшая задержка по заказу #{num} — курьер будет примерно на 15 минут позже. Спасибо за ожидание! 🙏",
                CTX["kb_main"]())
        except Exception as e:
            print(f"[dashboard] задержка #{num}: {e}")
        info["delay_notified_at"] = time.time()
        return (True, "Клиенту отправлено уведомление о задержке.") if ok \
            else (False, "VK не принял сообщение, попробуйте ещё раз.")

    # 3) Курьер завершает только доставку, которую уже везёт.
    if is_courier and not (is_delivery and current == "🚗 Курьер выехал" and requested == "✅ Доставлен"):
        return False, "Курьер может отметить только «Доставлен» после выезда."

    if current in FINAL:
        return False, f"Заказ уже завершён: {current}."
    flow = FLOW_DELIVERY if is_delivery else FLOW_PICKUP
    if requested not in flow.get(current, []):
        return False, f"Нельзя перейти {current} → {requested}."
    if requested == "❌ Отменён" and "Оплачено онлайн" in (info.get("payment_status") or ""):
        return False, "Заказ оплачен онлайн — отмена с возвратом делается в VK-боте."

    info["status"] = requested
    info["status_updated_at"] = time.time()
    CTX["save_json"](CTX["ACTIVE_ORDERS_FILE"], active)

    # Клиента уведомляем надёжно (с фоновой досылкой при сбое VK).
    text = CLIENT_TEXT.get(requested, f"Статус заказа #{num}: {requested}").format(n=num)
    _notify_client_reliable(num, info, text)
    return True, "OK"


def _set_stop(view_point, idx):
    if view_point not in CTX["STOP_POINTS"]:
        return False, "Неизвестная точка."
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return False, "Не указан номер позиции."
    res = CTX["toggle_stop"](view_point, i)
    return (True, res) if res else (False, "Неверный номер позиции.")


def _set_load(minutes):
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return False, "Не указана загрузка."
    if m not in (45, 60, 75):
        return False, "Допустимо 45, 60 или 75."
    CTX["save_kitchen_load"](m)
    return True, "OK"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # не спамим в логи Railway

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def _token(self):
        # токен передаём заголовком X-Token
        return self.headers.get("X-Token", "")

    def do_GET(self):
        try:
            self._route_get()
        except Exception as e:
            print(f"[dashboard] GET {self.path}: {e}")
            self._json(400, {"error": "bad request"})

    def _route_get(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            point = _auth(self._token())
            if not point:
                self._json(401, {"error": "unauthorized"})
                return
            self._json(200, _state_payload(point))
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            self._route_post()
        except Exception as e:
            print(f"[dashboard] POST {self.path}: {e}")
            self._json(400, {"ok": False, "msg": "Некорректный запрос."})

    def _route_post(self):
        path = self.path.split("?")[0]
        body = self._body_json()

        if path == "/api/login":
            pin = str(body.get("pin", ""))
            point = DASHBOARD_PINS.get(pin)
            if not point:
                self._json(401, {"error": "Неверный ПИН"})
                return
            self._json(200, {"token": _new_token(point), "point": point})
            return

        point = _auth(self._token())
        if not point:
            self._json(401, {"error": "unauthorized"})
            return

        if path == "/api/status":
            ok, msg = _apply_status(str(body.get("num")), str(body.get("status")), point)
            self._json(200 if ok else 400, {"ok": ok, "msg": msg})
            return
        if path == "/api/stop":
            vp = str(body.get("point"))
            if point != "all" and vp != point:
                self._json(403, {"ok": False, "msg": "Только своя точка."})
                return
            ok, msg = _set_stop(vp, body.get("idx"))
            self._json(200 if ok else 400, {"ok": ok, "msg": msg})
            return
        if path == "/api/load":
            # Загрузку меняет только владелец или точка доставки (кухня доставки).
            if point != "all" and point != CTX.get("DELIVERY_POINT"):
                self._json(403, {"ok": False, "msg": "Загрузку меняет только владелец."})
                return
            ok, msg = _set_load(body.get("minutes"))
            self._json(200 if ok else 400, {"ok": ok, "msg": msg})
            return

        self._json(404, {"error": "not found"})


def start_dashboard(ctx, port=None):
    """Запускает веб-панель в отдельном потоке. Не блокирует."""
    global CTX
    CTX = ctx
    port = int(port or os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)

    def _run():
        print(f"[dashboard] панель слушает порт {port}")
        server.serve_forever()

    threading.Thread(target=_run, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# Фронтенд: один адаптивный HTML со встроенным JS. Опрос /api/state раз в 4 сек.
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eat to End — панель</title>
<style>
:root{--bg:#0f1115;--card:#1a1e27;--card2:#222735;--line:#2c3244;--txt:#e8ebf2;--mut:#9aa3b5;
--acc:#ff7a1a;--ok:#22c55e;--warn:#eab308;--pri:#3b82f6;--neg:#ef4444;}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:var(--bg);color:var(--txt)}
header{position:sticky;top:0;background:#12151c;border-bottom:1px solid var(--line);
display:flex;align-items:center;gap:12px;padding:10px 14px;z-index:5;flex-wrap:wrap}
header .brand{font-weight:700;color:var(--acc)}
header .sp{flex:1}
header .pt{color:var(--mut);font-size:14px}
button{font:inherit;cursor:pointer;border:none;border-radius:10px;padding:9px 12px;color:#fff;background:var(--card2)}
button:active{transform:translateY(1px)}
.tabs{display:flex;gap:6px;padding:10px 14px;flex-wrap:wrap}
.tab{background:var(--card2);color:var(--mut)}
.tab.on{background:var(--acc);color:#111}
.wrap{padding:0 14px 40px;max-width:1100px;margin:0 auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.badge{font-size:12px;padding:3px 8px;border-radius:999px;background:var(--card2);color:var(--mut)}
.badge.d{background:#20304a;color:#9cc4ff}
.num{font-weight:700;font-size:18px}
.st{font-weight:600}
.mut{color:var(--mut);font-size:13px}
.det{white-space:pre-wrap;font-size:13px;color:#c7cede;background:#12151c;border-radius:10px;
padding:10px;margin-top:8px;max-height:150px;overflow:auto;display:none}
.det.on{display:block}
.mut a{color:#8ab4ff;text-decoration:none} .mut a:active{opacity:.6}
.acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.b-go{background:var(--pri)} .b-ok{background:var(--ok);color:#08210f} .b-warn{background:var(--warn);color:#2a2205}
.b-neg{background:var(--neg)} .b-gray{background:var(--card2);color:var(--mut)}
.stopitem{display:flex;align-items:center;justify-content:space-between;gap:8px;
padding:8px 10px;border-bottom:1px solid var(--line)}
.stopitem:last-child{border-bottom:none}
.pill{font-size:12px;padding:3px 9px;border-radius:999px}
.pill.on{background:#3b1a1a;color:#ff9c9c} .pill.off{background:#16301f;color:#9cf0bd}
.loadbtns{display:flex;gap:8px;flex-wrap:wrap}
.load{padding:14px 18px;border-radius:12px;font-weight:700;background:var(--card2)}
.load.sel{outline:3px solid var(--acc)}
.center{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.login{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;width:320px;max-width:100%}
input{font:inherit;width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);
background:#12151c;color:var(--txt);text-align:center;letter-spacing:4px;font-size:20px}
.hint{color:var(--mut);font-size:13px;margin-top:10px;text-align:center}
.seg{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.seg .s{background:var(--card2);color:var(--mut)} .seg .s.on{background:var(--pri);color:#fff}
.empty{color:var(--mut);text-align:center;padding:30px}
.toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);background:#000a;
border:1px solid var(--line);padding:10px 16px;border-radius:12px;opacity:0;transition:.2s;pointer-events:none}
.toast.on{opacity:1}
</style>
</head>
<body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<script>
const S={openDetails:new Set(),token:localStorage.getItem('ete_tok')||'',point:localStorage.getItem('ete_pt')||'',
tab:'orders',data:null,seen:new Set(),stopPoint:null,sound:true};

function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('on');
setTimeout(()=>t.classList.remove('on'),1800);}

function beep(){if(!S.sound)return;try{const a=new (window.AudioContext||window.webkitAudioContext)();
const o=a.createOscillator(),g=a.createGain();o.connect(g);g.connect(a.destination);
o.type='sine';o.frequency.value=880;g.gain.value=0.08;o.start();
o.frequency.setValueAtTime(660,a.currentTime+0.12);setTimeout(()=>{o.stop();a.close();},260);}catch(e){}}

async function api(path,method,body){
 const h={'Content-Type':'application/json'};if(S.token)h['X-Token']=S.token;
 const r=await fetch(path,{method:method||'GET',headers:h,body:body?JSON.stringify(body):undefined});
 if(r.status===401){logout();throw new Error('auth');}
 return r.json();
}

function logout(){S.token='';localStorage.removeItem('ete_tok');localStorage.removeItem('ete_pt');render();}

async function login(){
 const pin=document.getElementById('pin').value.trim();
 try{const r=await api('/api/login','POST',{pin});
   if(r.token){S.token=r.token;S.point=r.point;localStorage.setItem('ete_tok',r.token);
   localStorage.setItem('ete_pt',r.point);S.tab='orders';S.data=null;render();tick();}
   else toast(r.error||'Неверный ПИН');
 }catch(e){toast('Неверный ПИН');}
}

function toggleDet(num){ if(S.openDetails.has(num))S.openDetails.delete(num); else S.openDetails.add(num); render(); }

async function setStatus(num,status){
 if(status.indexOf('Отмен')>=0 && !confirm('Отменить заказ #'+num+'?'))return;
 if(status.indexOf('Доставлен')>=0 && !confirm('Отметить заказ #'+num+' доставленным?'))return;
 try{const r=await api('/api/status','POST',{num,status});
   toast(r.ok?('Готово: '+status):(r.msg||'Ошибка'));tick();}catch(e){}
}
async function toggleStop(point,idx){
 try{const r=await api('/api/stop','POST',{point,idx});toast(r.msg||'');tick();}catch(e){}
}
async function setLoad(min){
 try{const r=await api('/api/load','POST',{minutes:min});toast(r.ok?('Загрузка: '+min+' мин'):(r.msg));tick();}catch(e){}
}

async function tick(){
 if(!S.token)return;
 try{const d=await api('/api/state');
   // звук на новый заказ
   const cur=new Set(d.orders.map(o=>o.num));
   if(S.data){d.orders.forEach(o=>{if(!S.seen.has(o.num)){beep();}});}
   S.seen=cur;S.data=d;
   if(!S.stopPoint)S.stopPoint=d.stop.default_point;
   render();
 }catch(e){}
}

function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

function orderCard(o){
 const actBtns=o.actions.map(a=>{
   let cls='b-go';if(a.includes('Готов'))cls='b-ok';if(a.includes('Отмен'))cls='b-neg';if(a.includes('Курьер'))cls='b-go';if(a.includes('Доставлен'))cls='b-ok';if(a.includes('Задерж'))cls='b-warn';
   let dis='';let title='';
   if(a==='❌ Отменён'&&o.paid_online){dis='disabled title="Отмена оплаченного — в VK"';cls='b-gray';}
   return `<button class="${cls}" ${dis} onclick="setStatus('${o.num}','${a}')">${a}</button>`;
 }).join('');
 const payTag=o.paid_online?'<span class="badge" style="background:#16301f;color:#9cf0bd">оплачено</span>':'';
 return `<div class="card">
   <div class="row"><span class="num">#${o.num}</span>
     <span class="badge ${o.type==='Доставка'?'d':''}">${o.type}</span>
     <span class="badge">${esc(o.point)}</span>${payTag}<span class="sp" style="flex:1"></span>
     <span class="st">${esc(o.status)}</span></div>
   ${(o.time||o.total!=null||o.pay)?`<div class="mut" style="margin-top:6px">${o.time?'🕒 '+esc(o.time):''}${o.total!=null?' · 💰 '+o.total+'₽':''}${o.pay?' · 💳 '+esc(o.pay):''}</div>`:''}
   ${o.address?`<div class="mut">🏠 <a href="https://yandex.ru/maps/?text=${encodeURIComponent([o.zone,o.address].filter(Boolean).join(', '))}" target="_blank" rel="noopener">${esc(o.address)}</a></div>`:''}
   ${o.phone?`<div class="mut">📱 <a href="tel:${o.phone.replace(/[^0-9+]/g,'')}">${esc(o.phone)}</a></div>`:''}
   ${o.comment?`<div class="mut">💬 ${esc(o.comment)}</div>`:''}
   <div class="acts">${actBtns||'<span class="mut">— завершён —</span>'}
     <button class="b-gray" onclick="toggleDet('${o.num}')">детали</button>
   </div>
   <div class="det ${S.openDetails.has(o.num)?'on':''}">${esc(o.details)}</div>
 </div>`;
}

function view(){
 const d=S.data;if(!d)return '<div class="empty">Загрузка…</div>';
 if(S.tab==='orders'){
   const act=d.orders.filter(o=>!o.final), done=d.orders.filter(o=>o.final);
   const body=act.length?act.map(orderCard).join(''):'<div class="empty">Активных заказов нет</div>';
   const doneBody=done.length?('<h3 class="mut">Завершённые</h3><div class="grid">'+done.slice(0,12).map(orderCard).join('')+'</div>'):'';
   return `<div class="grid">${body}</div>${doneBody}`;
 }
 if(S.tab==='stop'){
   const pts=d.stop.points;const vp=(S.stopPoint&&pts.includes(S.stopPoint))?S.stopPoint:d.stop.default_point;
   const stopped=new Set(d.stop.stopped_by[vp]||[]);
   const seg=pts.length>1?pts.map(p=>`<button class="s ${p===vp?'on':''}" onclick="S.stopPoint='${p}';render()">${esc(p)}</button>`).join(''):'';
   const items=d.stop.items.map(it=>{const isStop=stopped.has(it.name);
     return `<div class="stopitem">
     <div><b>${it.idx}.</b> ${esc(it.name)} <span class="mut">· ${esc(it.cat)}</span></div>
     <button class="pill ${isStop?'on':'off'}" onclick="toggleStop('${vp}',${it.idx})">${isStop?'⛔ в стопе':'✅ в продаже'}</button>
   </div>`;}).join('');
   return `<div class="seg">${seg}</div><div class="card" style="padding:0">${items}</div>`;
 }
 if(S.tab==='load'){
   const cur=d.load;
   const b=[45,60,75].map(m=>`<button class="load ${cur===m?'sel':''}" onclick="setLoad(${m})">${m===45?'🟢':m===60?'🟡':'🔴'} ${m} мин</button>`).join('');
   return `<div class="card"><div class="mut">Оценка «Побыстрее» на доставке. Берётся максимум из этого и часов пик.</div>
     <div class="loadbtns" style="margin-top:12px">${b}</div>
     <div class="hint" style="text-align:left;margin-top:10px">Сейчас: <b>${cur} мин</b></div></div>`;
 }
 if(S.tab==='clients'){
   const c=d.clients;
   if(!c.length)return '<div class="empty">Клиентов пока нет</div>';
   const rows=c.map(x=>`<div class="card">
     <div class="row"><b>📱 ${esc(x.phone||'—')}</b><span class="sp" style="flex:1"></span>
       <span class="badge">${esc(x.last_type)}</span></div>
     <div class="mut">Последняя точка: ${esc(x.last_point||'—')} · позиций: ${x.items_count}</div>
     ${x.address?`<div class="mut">🏠 ${esc(x.address)}</div>`:''}
   </div>`).join('');
   return `<div class="grid">${rows}</div>`;
 }
 return '';
}

function render(){
 const app=document.getElementById('app');
 if(!S.token){
   app.innerHTML=`<div class="center"><div class="login">
     <div class="brand" style="font-size:22px;color:var(--acc);text-align:center;font-weight:800">Eat to End</div>
     <div class="hint">Введите ПИН точки</div>
     <div style="margin-top:14px"><input id="pin" type="tel" inputmode="numeric" placeholder="••••" autofocus></div>
     <button class="b-go" style="width:100%;margin-top:12px" onclick="login()">Войти</button>
   </div></div>`;
   const p=document.getElementById('pin');if(p)p.addEventListener('keydown',e=>{if(e.key==='Enter')login();});
   return;
 }
 const d=S.data;
 const isCourier=(S.point==='courier');
 const tabs=isCourier?[['orders','Мои доставки']]:[['orders','Заказы'],['stop','Стоп-лист'],['load','Загрузка'],['clients','Клиенты']];
 if(isCourier)S.tab='orders';
 const roleName=S.point==='all'?'все точки':(S.point==='courier'?'курьер':S.point);
 app.innerHTML=`<header>
   <span class="brand">Eat to End</span>
   <span class="pt">${esc(roleName)}</span>
   <span class="sp"></span>
   <button class="b-gray" onclick="S.sound=!S.sound;toast(S.sound?'Звук вкл':'Звук выкл')">🔔</button>
   <button class="b-gray" onclick="logout()">Выход</button>
 </header>
 <div class="tabs">${tabs.map(t=>`<button class="tab ${S.tab===t[0]?'on':''}" onclick="S.tab='${t[0]}';render()">${t[1]}${t[0]==='orders'&&d?` (${d.orders.filter(o=>!o.final).length})`:''}</button>`).join('')}</div>
 <div class="wrap">${view()}</div>`;
}

render();
if(S.token)tick();
setInterval(tick,4000);
</script>
</body>
</html>"""

(function () {
  "use strict";

  var CITIES = [];
  var AGE_LABELS = {};
  var state = { city: null, group: null, my_gender: "m", want_gender: "m", age: null };
  var ws = null;
  var wsOpen = false;
  var reconnectTimer = null;
  var typingTimer = null;
  var inChat = false;   // true = стоим в комнате с собеседником
  var searching = false;

  var el = {
    navOnline: document.getElementById("navOnline"),
    lobby: document.getElementById("lobby"),
    searchingDiv: document.getElementById("searching"),
    searchDesc: document.getElementById("searchDesc"),
    searchWait: document.getElementById("searchWait"),
    startBtn: document.getElementById("startBtn"),
    cancelBtn: document.getElementById("cancelBtn"),
    citySearch: document.getElementById("citySearch"),
    citySelect: document.getElementById("citySelect"),
    cityStatus: document.getElementById("cityStatus"),
    ageInput: document.getElementById("ageInput"),
    chatWrap: document.getElementById("chatWrap"),
    roomLabel: document.getElementById("roomLabel"),
    partnerInfo: document.getElementById("partnerInfo"),
    nextBtn: document.getElementById("nextBtn"),
    leaveBtn: document.getElementById("leaveBtn"),
    messages: document.getElementById("messages"),
    input: document.getElementById("input"),
    sendBtn: document.getElementById("sendBtn"),
  };

  /********** Загрузка данных **********/
  fetch("api/bootstrap")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      CITIES = data.cities || [];
      AGE_LABELS = data.labels || {};
      fillCitySelect(CITIES);
      el.navOnline.textContent = data.online || 0;
      applySavedCity();
      initGeoDetect();
    })
    .catch(function () {
      setCityStatus("Сервер недоступен. Проверь, что он запущен.", true);
    });

  function fillCitySelect(list) {
    el.citySelect.innerHTML = "";
    list.forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      el.citySelect.appendChild(opt);
    });
  }

  function setCityStatus(text, isErr) {
    el.cityStatus.textContent = text;
    el.cityStatus.className = "city-status" + (isErr ? " err" : "");
  }

  /********** ГЕО-ПОДТВЕРЖДЕНИЕ ГОРОДА **********/
  // 1) Пытаемся определить город по IP (бесплатный сервис).
  // 2) Подставляем его в список и помечаем как подтверждённый.
  // 3) Пользователь всегда может выбрать другой город из списка.
  // 4) Сохраняем выбор в localStorage — он переживает перезагрузку страницы.
  // Важно: сервер принимает ТОЛЬКО город из своего списка (валидация на бэкенде),
  // поэтому «придумать» город нельзя — он просто не сохранится.

  function initGeoDetect() {
    if (localStorage.getItem("nekto_city")) {
      return; // уже сохранён
    }
    var done = false;
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", "https://ipapi.co/json/");
      xhr.timeout = 6000;
      xhr.onload = function () {
        if (done) return;
        done = true;
        try {
          var data = JSON.parse(xhr.responseText);
          var name = (data.city || "").trim();
          if (name && CITIES.indexOf(name) !== -1) {
            selectCity(name);
            setCityStatus("Город определён автоматически: " + name);
          } else {
            hintManualCity();
          }
        } catch (e) { hintManualCity(); }
      };
      xhr.onerror = function () { if (!done) { done = true; hintManualCity(); } };
      xhr.ontimeout = function () { if (!done) { done = true; hintManualCity(); } };
      xhr.send();
    } catch (e) { hintManualCity(); }
  }

  function hintManualCity() {
    if (!el.citySelect.value) {
      setCityStatus("Укажи свой город из списка", false);
    }
  }

  function selectCity(name) {
    var found = CITIES.indexOf(name) !== -1;
    if (found) {
      el.citySelect.value = name;
      state.city = name;
    }
    return found;
  }

  function applySavedCity() {
    var saved = localStorage.getItem("nekto_city");
    if (saved && CITIES.indexOf(saved) !== -1) {
      selectCity(saved);
      setCityStatus("Город: " + saved);
    } else {
      setCityStatus("Выбери город", false);
    }
  }

  // Поиск по подстроке
  el.citySearch.addEventListener("input", function () {
    var q = el.citySearch.value.trim().toLowerCase();
    var filtered = q
      ? CITIES.filter(function (c) { return c.toLowerCase().indexOf(q) !== -1; })
      : CITIES;
    fillCitySelect(filtered);
    if (filtered.length === 1) {
      el.citySelect.value = filtered[0];
    }
  });

  // Выбор города из списка = подтверждение
  el.citySelect.addEventListener("change", function () {
    var v = el.citySelect.value;
    if (v && CITIES.indexOf(v) !== -1) {
      state.city = v;
      localStorage.setItem("nekto_city", v);
      setCityStatus("Город подтверждён: " + v);
    }
  });

  /********** Пол и возраст **********/
  function bindSeg(selector, cb) {
    document.querySelectorAll(selector).forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(selector).forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        cb(btn.dataset);
      });
    });
  }

  bindSeg(".seg-btn[data-gender]", function (d) { state.my_gender = d.gender; });
  bindSeg(".seg-btn[data-want]", function (d) { state.want_gender = d.want; });

  // определение возрастной группы по введённому возрасту
  function groupForAge(age) {
    if (age === null || isNaN(age)) return null;
    if (age <= 14) return "u15";
    if (age <= 18) return "15-18";
    if (age <= 25) return "18-25";
    if (age <= 35) return "25-35";
    return "35+";
  }

  el.ageInput.addEventListener("input", function () {
    var v = parseInt(el.ageInput.value, 10);
    if (!isNaN(v) && v >= 5 && v <= 99) {
      state.age = v;
      state.group = groupForAge(v);
    } else {
      state.age = null;
      state.group = null;
    }
  });

  /********** Экран поиска **********/
  function showSearching() {
    el.lobby.classList.add("hidden");
    el.chatWrap.classList.add("hidden");
    el.searchingDiv.classList.remove("hidden");
    searching = true;
    var want = state.want_gender === "m" ? "мужчину" : state.want_gender === "f" ? "женщину" : "собеседника";
    var gLabel = AGE_LABELS[state.group] || "";
    el.searchDesc.textContent = state.city + " · " + gLabel + " · " + want;
    el.searchWait.textContent = "Как только найдётся человек с такими же параметрами — вы сразу окажетесь в паре. Обычно это занимает меньше минуты.";
  }

  function hideSearching() {
    el.searchingDiv.classList.add("hidden");
    searching = false;
  }

  el.cancelBtn.addEventListener("click", function () {
    send({ type: "cancel" });
    hideSearching();
    el.lobby.classList.remove("hidden");
  });

  /********** Старт **********/
  el.startBtn.addEventListener("click", function () {
    // проверки
    if (!state.city) {
      setCityStatus("Выбери город из списка — без этого поиск не запустится.", true);
      return;
    }
    if (!state.group) {
      var v = parseInt(el.ageInput.value, 10);
      if (isNaN(v) || v < 5 || v > 99) {
        setCityStatus("Введи свой возраст (от 5 до 99 лет).", true);
        return;
      }
      state.age = v;
      state.group = groupForAge(v);
    }
    if (!state.group) return;
    connect();
    showSearching();
  });

  el.nextBtn.addEventListener("click", function () {
    nextPartner();
  });

  el.leaveBtn.addEventListener("click", function () {
    send({ type: "cancel" });
    resetToLobby();
  });

  function nextPartner() {
    if (!wsOpen) return;
    inChat = false;
    send({ type: "next" });
    showSearching();
    el.messages.innerHTML = "";
  }

  function resetToLobby() {
    inChat = false;
    if (ws) { try { ws.close(); } catch (e) {} }
    ws = null;
    wsOpen = false;
    el.chatWrap.classList.add("hidden");
    el.searchingDiv.classList.add("hidden");
    el.lobby.classList.remove("hidden");
    el.messages.innerHTML = "";
  }

  /********** WebSocket **********/
  function wsUrl() {
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    return proto + location.host + "/ws";
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    try { ws = new WebSocket(wsUrl()); } catch (e) { return; }

    ws.onopen = function () {
      wsOpen = true;
      send({
        type: "find",
        city: state.city,
        group: state.group,
        my_gender: state.my_gender,
        want_gender: state.want_gender,
        age: state.age
      });
    };

    ws.onmessage = function (ev) {
      var m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      handle(m);
    };

    ws.onclose = function () {
      wsOpen = false;
      if (!document.getElementById("lobby").classList.contains("hidden") && !searching && !inChat) {
        return; // мы на лобби — ничего не делаем
      }
      showSystem("Соединение потеряно. Переподключаюсь…");
      scheduleReconnect();
    };

    ws.onerror = function () {};
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(function () {
      if (searching || inChat) connect();
    }, 2500);
  }

  function send(obj) {
    if (ws && wsOpen) {
      try { ws.send(JSON.stringify(obj)); } catch (e) {}
    }
  }

  /********** Обработка сообщений сервера **********/
  function handle(m) {
    switch (m.type) {
      case "searching":
        searching = true;
        break;
      case "matched":
        searching = false;
        hideSearching();
        enterChat(m);
        break;
      case "partner_left":
        showSystem("Собеседник покинул чат.", true);
        inChat = false;
        // автоматически ищем следующего
        send({ type: "next" });
        showSearching();
        el.messages.innerHTML = "";
        break;
      case "cancelled":
        break;
      case "msg":
        addMsg(m.text, false);
        break;
      case "typing":
        break;
      case "error":
        showSystem(m.text || "Ошибка");
        break;
    }
  }

  function enterChat(m) {
    inChat = true;
    el.chatWrap.classList.remove("hidden");
    el.searchingDiv.classList.add("hidden");
    el.roomLabel.textContent = m.city || state.city;
    el.partnerInfo.textContent = state.city + " · " + (AGE_LABELS[state.group] || "");
    el.messages.innerHTML = "";
    addSystem("Собеседник найден! Начни разговор. Собеседник видит только твои сообщения и не знает, кто ты.");
    el.input.focus();
  }

  function addMsg(text, mine) {
    var row = document.createElement("div");
    row.className = "msg" + (mine ? " mine" : "");
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    var head = document.createElement("div");
    head.className = "head";
    head.textContent = mine ? "ты" : "собеседник";
    row.appendChild(bubble);
    row.appendChild(head);
    el.messages.appendChild(row);
    scrollBottom();
  }

  function addSystem(text) {
    var row = document.createElement("div");
    row.className = "system-line";
    row.textContent = text;
    el.messages.appendChild(row);
    scrollBottom();
  }

  function showSystem(text, partner) {
    var row = document.createElement("div");
    row.className = "system-line" + (partner ? " partner" : "");
    row.textContent = text;
    el.messages.appendChild(row);
    scrollBottom();
  }

  function scrollBottom() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  /********** Отправка **********/
  function sendMessage() {
    var text = el.input.value.trim();
    if (!text || !wsOpen || !inChat) return;
    send({ type: "msg", text: text });
    addMsg(text, true);
    el.input.value = "";
    el.input.style.height = "auto";
  }

  el.sendBtn.addEventListener("click", sendMessage);

  el.input.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      sendMessage();
    }
  });

  el.input.addEventListener("input", function () {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 120) + "px";
    clearTimeout(typingTimer);
    typingTimer = setTimeout(function () {
      send({ type: "typing" });
    }, 400);
  });

  window.addEventListener("beforeunload", function () {
    if (ws) { try { ws.close(); } catch (e) {} }
  });
})();
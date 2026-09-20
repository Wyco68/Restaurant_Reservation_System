/*
 * TableFlow frontend - vanilla JS, no framework.
 *
 * Serve it separately from the API, e.g.:
 *     python -m http.server 5500 --directory frontend
 * Port 5500 is the origin allowed by the API's CORS config.
 */

const API = "http://localhost:8000";

const state = {
  token: null,
  restaurantId: null,
  restaurantPage: 1,
  productPage: 1,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;

  const res = await fetch(`${API}${path}`, { ...options, headers });
  const body = res.status === 204 ? null : await res.json().catch(() => null);

  if (!res.ok) {
    // Surface the server's own message. The API returns {"detail": "..."}
    // consistently, so the UI never has to guess what went wrong.
    const message = body?.detail || `HTTP ${res.status}`;
    throw Object.assign(new Error(message), { status: res.status });
  }
  return body;
}

/* ------------------------------------------------------------- health --- */

async function checkHealth() {
  const el = $("health");
  try {
    const h = await api("/health");
    const ok = h.postgres && h.mongodb;
    el.textContent = `API ${h.status} · PostgreSQL ${h.postgres ? "up" : "down"} · MongoDB ${h.mongodb ? "up" : "down"}`;
    el.className = `health ${ok ? "ok" : "warn"}`;
  } catch {
    el.textContent = "API unreachable — is uvicorn running?";
    el.className = "health error";
  }
}

/* --------------------------------------------------------------- auth --- */

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("auth-status");
  try {
    const r = await api("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: $("email").value, password: $("password").value }),
    });
    state.token = r.access_token;
    const me = await api("/api/v1/users/me/profile");
    status.textContent = `Signed in as ${me.full_name} (${me.role})`;
    status.className = "status ok";
  } catch (err) {
    state.token = null;
    status.textContent = `Sign-in failed: ${err.message}`;
    status.className = "status error";
  }
});

/* -------------------------------------------------------- restaurants --- */

async function loadRestaurants(page = 1) {
  state.restaurantPage = page;
  const params = new URLSearchParams({ page, limit: 6 });
  if ($("filter-city").value) params.set("city", $("filter-city").value);
  if ($("filter-cuisine").value) params.set("cuisine", $("filter-cuisine").value);

  const data = await api(`/api/v1/restaurants?${params}`);
  const list = $("restaurant-list");
  list.innerHTML = "";

  if (!data.items.length) {
    list.innerHTML = '<p class="empty">No restaurants match those filters.</p>';
  }

  data.items.forEach((r) => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${escapeHtml(r.name)}</h3>
      <p>${escapeHtml(r.cuisine)} · ${escapeHtml(r.city)}</p>
      <p class="meta">${"$".repeat(r.price_range)} · ${r.avg_rating ?? "–"} ★</p>`;
    card.addEventListener("click", () => selectRestaurant(r));
    list.appendChild(card);
  });

  renderPager("restaurant-pager", data, loadRestaurants);
}

async function selectRestaurant(restaurant) {
  state.restaurantId = restaurant.id;
  $("menu-context").textContent = `— ${restaurant.name}`;
  await loadProducts(1);
  await loadTables();
}

/* ------------------------------------------------------------ products --- */

async function loadProducts(page = 1) {
  if (!state.restaurantId) return;
  state.productPage = page;

  const data = await api(
    `/api/v1/products?restaurant_id=${state.restaurantId}&page=${page}&limit=8`
  );
  const list = $("product-list");
  list.innerHTML = "";

  data.items.forEach((p) => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${escapeHtml(p.name)}</h3>
      <p class="meta">${escapeHtml(p.category)}</p>
      <p class="price">${Number(p.price).toFixed(2)} ${escapeHtml(p.currency)}</p>`;
    list.appendChild(card);
  });

  renderPager("product-pager", data, loadProducts);
}

/* -------------------------------------------------------- reservations --- */

async function loadTables() {
  const select = $("table-select");
  const startsAt = $("starts-at").value;
  if (!state.restaurantId || !startsAt) {
    select.innerHTML = '<option value="">Pick a restaurant and time</option>';
    return;
  }

  const start = new Date(startsAt);
  const end = new Date(start.getTime() + 90 * 60 * 1000);
  const slots = await api(
    `/api/v1/restaurants/${state.restaurantId}/availability` +
      `?starts_at=${encodeURIComponent(start.toISOString())}` +
      `&ends_at=${encodeURIComponent(end.toISOString())}` +
      `&party_size=${$("party-size").value}`
  );

  select.innerHTML = slots.length
    ? ""
    : '<option value="">No tables for that time</option>';

  slots.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.restaurant_table_id;
    opt.textContent = `${s.table_number} (seats ${s.capacity})${s.available ? "" : " — taken"}`;
    opt.disabled = !s.available;
    select.appendChild(opt);
  });
}

$("starts-at").addEventListener("change", loadTables);
$("party-size").addEventListener("change", loadTables);

$("booking-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("booking-status");

  if (!state.token) {
    status.textContent = "Sign in first.";
    status.className = "status error";
    return;
  }

  const start = new Date($("starts-at").value);
  const end = new Date(start.getTime() + 90 * 60 * 1000);

  try {
    const r = await api("/api/v1/reservations", {
      method: "POST",
      body: JSON.stringify({
        restaurant_id: state.restaurantId,
        restaurant_table_id: Number($("table-select").value),
        guest_name: $("guest-name").value,
        party_size: Number($("party-size").value),
        starts_at: start.toISOString(),
        ends_at: end.toISOString(),
      }),
    });
    status.textContent = `Reserved — booking #${r.id} for ${r.guest_name}.`;
    status.className = "status ok";
    await loadTables();
  } catch (err) {
    // 409 is the double-booking case and deserves its own message, because
    // it is the one error a user can actually act on.
    status.textContent =
      err.status === 409
        ? "That table was just taken by someone else. Pick another."
        : `Booking failed: ${err.message}`;
    status.className = "status error";
  }
});

/* --------------------------------------------------------------- misc --- */

function renderPager(elementId, data, loader) {
  const pager = $(elementId);
  pager.innerHTML = "";
  if (data.pages <= 1) return;

  const prev = document.createElement("button");
  prev.textContent = "‹ Prev";
  prev.disabled = data.page <= 1;
  prev.onclick = () => loader(data.page - 1);

  const label = document.createElement("span");
  label.textContent = `Page ${data.page} of ${data.pages} (${data.total} total)`;

  const next = document.createElement("button");
  next.textContent = "Next ›";
  next.disabled = data.page >= data.pages;
  next.onclick = () => loader(data.page + 1);

  pager.append(prev, label, next);
}

function escapeHtml(s) {
  // Everything from the API is rendered through this. Server data is not
  // automatically trustworthy just because it is ours.
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

function populateFilters() {
  const cities = ["Chiang Mai", "Bangkok", "Phuket", "Khon Kaen", "Hat Yai"];
  const cuisines = ["Thai", "Japanese", "Italian", "Indian", "Chinese",
                    "Korean", "Mexican", "Vietnamese", "French", "Vegan"];
  cities.forEach((c) => $("filter-city").add(new Option(c, c)));
  cuisines.forEach((c) => $("filter-cuisine").add(new Option(c, c)));
}

$("search-btn").addEventListener("click", () => loadRestaurants(1));

populateFilters();
checkHealth();
loadRestaurants(1).catch(() => {});

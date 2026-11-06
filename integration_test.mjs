// Simulates exactly what the React app's hooks call on each page mount,
// against the real live backend, and checks the field names the JSX reads.
// Override with: API_URL=http://127.0.0.1:8001 node integration_test.mjs
const API_BASE = `${process.env.API_URL || "http://127.0.0.1:8000"}/api/v1`;
// Unique suffix so the test can be re-run against the same database.
const RUN = Date.now().toString().slice(-8);

async function apiFetch(path, { method = "GET", body, token, params } = {}) {
  let url = `${API_BASE}${path}`;
  if (params) {
    const clean = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
    const qs = new URLSearchParams(clean).toString();
    if (qs) url += `?${qs}`;
  }
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail || detail; } catch {}
    throw new Error(`${method} ${path} -> ${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

let passed = 0, failed = 0;
function assert(cond, label) {
  if (cond) { passed++; console.log(`[OK] ${label}`); }
  else { failed++; console.log(`[FAIL] ${label}`); }
}

const run = async () => {
  // --- Login screen ---
  const superLogin = await apiFetch("/auth/login", { method: "POST", body: { email: "deepa@relaycrm.io", password: "password123" } });
  assert(superLogin.access_token && superLogin.role === "super_admin" && superLogin.name, "Login returns access_token/role/name (super admin)");
  const superToken = superLogin.access_token;

  const adminLogin = await apiFetch("/auth/login", { method: "POST", body: { email: "rahul.sharma@relaycrm.io", password: "password123" } });
  assert(adminLogin.role === "admin", "Login returns role=admin for admin user");
  const adminToken = adminLogin.access_token;

  // --- SuperAdminDashboard: admins, numbers, analytics/summary ---
  const admins = await apiFetch("/admins", { token: superToken });
  assert(Array.isArray(admins) && admins.length > 0, "GET /admins returns array");
  const a0 = admins[0];
  assert(["name","numbers_count","messages_sent","replies","leads","conversion_rate","status"].every(k => k in a0),
    "admin object has all fields SuperAdminDashboard/AdminsPage read");

  const numbers = await apiFetch("/numbers", { token: superToken });
  assert(Array.isArray(numbers) && numbers.length >= 47, `GET /numbers returns at least the 47 seeded numbers (got ${numbers.length})`);
  const n0 = numbers[0];
  assert(["phone","display_name","status","messages_today","delivery_rate","reply_rate","last_activity_at"].every(k => k in n0),
    "number object has all fields NumbersPage/Dashboard read");

  const summary = await apiFetch("/analytics/summary", { token: superToken });
  assert(["messages_sent","delivered","read","failed","replies","leads","conversion_rate"].every(k => k in summary),
    "analytics summary has all fields KPI cards read");

  // --- NumbersPage: admin-scoped (non-super) should see fewer numbers ---
  const adminNumbers = await apiFetch("/numbers", { token: adminToken });
  assert(adminNumbers.length < numbers.length && adminNumbers.length > 0, `Admin-scoped numbers (${adminNumbers.length}) < all numbers (${numbers.length})`);

  // --- AdminsPage should 403 for an admin token ---
  try {
    await apiFetch("/admins", { token: adminToken });
    assert(false, "Admin token blocked from /admins");
  } catch (e) {
    assert(e.message.includes("403"), "Admin token blocked from /admins (403)");
  }

  // --- CrmPage: paginated contacts shape ---
  const contactsPage1 = await apiFetch("/contacts", { token: adminToken, params: { page: 1, per_page: 10 } });
  assert(["items","total","page","per_page","total_pages"].every(k => k in contactsPage1), "contacts response has pagination envelope");
  assert(Array.isArray(contactsPage1.items) && contactsPage1.items.length <= 10, "contacts items respects per_page");
  if (contactsPage1.items.length) {
    const c0 = contactsPage1.items[0];
    assert(["name","phone","email","status","tags","last_contact_at","created_at"].every(k => k in c0), "contact object has fields CrmPage reads");
  }

  // status filter
  const qualified = await apiFetch("/contacts", { token: adminToken, params: { page: 1, per_page: 50, status: "Qualified" } });
  assert(qualified.items.every(c => c.status === "Qualified"), "contacts status filter works server-side");

  // --- InboxPage: conversations with embedded contact ---
  const conversations = await apiFetch("/inbox/conversations", { token: adminToken });
  assert(Array.isArray(conversations), "GET /inbox/conversations returns array");
  if (conversations.length) {
    const conv0 = conversations[0];
    assert(conv0.contact && conv0.contact.name && conv0.contact.phone, "conversation has embedded contact.name/phone (the fix)");
    assert(typeof conv0.whatsapp_number_phone === "string", "conversation has whatsapp_number_phone");

    // conversation detail + messages
    const detail = await apiFetch(`/inbox/conversations/${conv0.id}`, { token: adminToken });
    assert(Array.isArray(detail.messages), "conversation detail has messages array");
    if (detail.messages.length) {
      const m0 = detail.messages[0];
      assert(["sender","type","text","created_at"].every(k => k in m0), "message object has fields Bubble component reads");
    }

    // send a real message through the mock WhatsApp service
    const sent = await apiFetch(`/inbox/conversations/${conv0.id}/messages`, {
      method: "POST", token: adminToken, body: { type: "text", text: "Integration test message" },
    });
    assert(sent.status === "sent", "Sending a message returns status=sent (mock WhatsApp service worked)");

    // conversation status should now be 'human' after a business reply
    const detailAfter = await apiFetch(`/inbox/conversations/${conv0.id}`, { token: adminToken });
    assert(detailAfter.status === "human", "Conversation status flips to 'human' after business reply");
  }

  // --- CampaignsPage: list + create + launch wizard ---
  const campaigns = await apiFetch("/campaigns", { token: adminToken });
  assert(Array.isArray(campaigns), "GET /campaigns returns array");
  const newCampaign = await apiFetch("/campaigns", {
    method: "POST", token: adminToken,
    body: { name: "Integration Test Campaign", whatsapp_number_id: adminNumbers[0].id, message_type: "text", message_text: "Hello!" },
  });
  assert(newCampaign.status === "Draft", "New campaign starts as Draft");
  const launched = await apiFetch(`/campaigns/${newCampaign.id}/launch`, { method: "POST", token: adminToken });
  assert(launched.status === "Running", "Campaign launch flips status to Running");

  // --- AutomationsPage: list + toggle status ---
  const automations = await apiFetch("/automations", { token: adminToken });
  assert(Array.isArray(automations), "GET /automations returns array");
  if (automations.length) {
    const auto0 = automations[0];
    const toggled = await apiFetch(`/automations/${auto0.id}`, { method: "PATCH", token: adminToken, body: { status: auto0.status === "Running" ? "Paused" : "Running" } });
    assert(toggled.status !== auto0.status, "Automation status toggle actually changes status");
  }

  // --- ChatbotPage ---
  const chatbots = await apiFetch("/chatbots", { token: adminToken });
  assert(Array.isArray(chatbots), "GET /chatbots returns array");
  if (chatbots.length) {
    assert(["name","status","conversations","automated_responses","human_handoffs","flow","keywords"].every(k => k in chatbots[0]), "chatbot object has fields ChatbotPage reads");
  }

  // --- AnalyticsPage: funnel ---
  const funnel = await apiFetch("/analytics/funnel", { token: adminToken });
  assert(Array.isArray(funnel) && funnel.every(f => "name" in f && "value" in f), "funnel data has name/value pairs");

  // --- ApiKeysPage: super-admin only, masked values, full lifecycle ---
  try {
    await apiFetch("/api-keys", { token: adminToken });
    assert(false, "Admin blocked from /api-keys");
  } catch (e) {
    assert(e.message.includes("403"), "Admin blocked from /api-keys (403)");
  }
  const newKey = await apiFetch("/api-keys", { method: "POST", token: superToken, body: { provider: "Meta", api_key: "RAW_SECRET_SHOULD_NEVER_LEAK_12345" } });
  assert(!JSON.stringify(newKey).includes("RAW_SECRET_SHOULD_NEVER_LEAK_12345"), "Raw API key never appears in create response");
  assert(newKey.masked.includes("2345"), "Masked key retains last 4 chars");
  const tested = await apiFetch(`/api-keys/${newKey.id}/test`, { method: "POST", token: superToken });
  assert(tested.status === "Connected", "API key test-connection returns Connected");
  const afterDelete = await apiFetch(`/api-keys/${newKey.id}`, { method: "DELETE", token: superToken });
  assert(afterDelete.deleted === true, "API key delete confirms deletion");

  // --- ActivityLogsPage: super-admin only, should now have entries from the actions above ---
  const logs = await apiFetch("/activity-logs", { token: superToken, params: { limit: 20 } });
  assert(Array.isArray(logs) && logs.length > 0, `Activity logs populated by the actions above (${logs.length} entries)`);

  // --- AddNumberModal: create a number and confirm it shows up ---
  const beforeCount = (await apiFetch("/numbers", { token: superToken })).length;
  await apiFetch("/numbers", { method: "POST", token: superToken, body: { phone: `+91 98${RUN}`, display_name: "Integration Test Line", admin_id: admins[0].id } });
  const afterCount = (await apiFetch("/numbers", { token: superToken })).length;
  assert(afterCount === beforeCount + 1, "Adding a number via the modal's exact payload shape increases the count");

  // --- AddAdminModal ---
  const newAdmin = await apiFetch("/admins", { method: "POST", token: superToken, body: { name: "Integration Test Admin", email: `integration.${RUN}@relaycrm.io`, phone: "+91 9000000000", password: "testpass123" } });
  assert(newAdmin.role === "admin", "New admin created with default role=admin");
  const newAdminLogin = await apiFetch("/auth/login", { method: "POST", body: { email: `integration.${RUN}@relaycrm.io`, password: "testpass123" } });
  assert(newAdminLogin.access_token, "Newly created admin can log in immediately");

  // --- Regression checks for bugs fixed in the Nov 2026 review ---
  const status = async (path, opts) => { try { await apiFetch(path, opts); return 200; } catch (e) { return Number((e.message.match(/-> (\d+)/) || [])[1]); } };
  const allNums = await apiFetch("/numbers", { token: superToken });
  const myNums = new Set((await apiFetch("/numbers", { token: adminToken })).map((n) => n.id));
  const foreign = allNums.find((n) => !myNums.has(n.id));
  const mine = allNums.find((n) => myNums.has(n.id));

  const saContact = await apiFetch("/contacts", { method: "POST", token: superToken, body: { name: "Regression SA", phone: `+91 97${RUN}`, whatsapp_number_id: foreign.id } });
  assert(saContact.admin_id === foreign.admin_id, "Super Admin can create a contact (owned by the number's admin, was a 500)");
  assert(await status("/contacts", { method: "POST", token: adminToken, body: { name: "x", phone: "+1", whatsapp_number_id: foreign.id } }) === 404, "Admin cannot create a contact on another admin's number");
  assert(await status("/campaigns", { method: "POST", token: adminToken, body: { name: "x", whatsapp_number_id: foreign.id } }) === 404, "Admin cannot create a campaign on another admin's number");
  assert(await status("/chatbots", { method: "POST", token: adminToken, body: { name: "x", whatsapp_number_id: foreign.id } }) === 404, "Admin cannot create a chatbot on another admin's number");
  assert(await status(`/numbers/${mine.id}`, { method: "PATCH", token: adminToken, body: { admin_id: foreign.admin_id } }) === 403, "Admin cannot reassign their number to someone else");

  const convs = await apiFetch("/inbox/conversations", { token: superToken });
  const note = await apiFetch(`/inbox/conversations/${convs[convs.length - 1].id}/messages`, { method: "POST", token: superToken, body: { type: "note", text: "internal note" } });
  assert(note.type === "note" && note.sender === "system", "Internal note is saved, not sent (was a 500)");

  await apiFetch(`/admins/${newAdmin.id}`, { method: "PATCH", token: superToken, body: { status: "inactive" } });
  assert(await status("/numbers", { token: newAdminLogin.access_token }) === 403, "Deactivated admin's existing token stops working");

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
};

run().catch(e => { console.error("UNCAUGHT:", e); process.exit(1); });

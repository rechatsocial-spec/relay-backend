import React, { useState, useRef, useEffect, useMemo, createContext, useContext } from "react";
import {
  LayoutGrid, Users, Phone, Inbox as InboxIcon, Contact, Megaphone, Workflow,
  Bot, BarChart3, ScrollText, Settings as SettingsIcon, Search, Bell, ChevronDown,
  Plus, X, Check, CheckCheck, Send, Paperclip, Image as ImageIcon, Video, Mic,
  Smile, MoreVertical, Play, Square, Trash2, Tag, UserCog,
  ChevronRight, ChevronLeft, Eye, Pencil, Power, LogOut, Key,
  CircleDot, Clock, Construction, ArrowDown, GitBranch, MessageCircle, Save,
  FlaskConical, Zap, ListTree, RefreshCw, AlertCircle, WifiOff
} from "lucide-react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Funnel, FunnelChart, LabelList
} from "recharts";

// ============================================================
// API CLIENT — every page below calls the real FastAPI backend.
// Change API_BASE if the backend isn't running on localhost:8000.
// ============================================================
const API_BASE = "http://localhost:8000/api/v1";

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function apiFetch(path, { method = "GET", body, token, params } = {}) {
  let url = `${API_BASE}${path}`;
  if (params) {
    const clean = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
    const qs = new URLSearchParams(clean).toString();
    if (qs) url += `?${qs}`;
  }
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch (networkErr) {
    // Fetch throws (not a 4xx/5xx) when the backend isn't reachable at all —
    // wrong host, server not running, CORS block. Surfaced distinctly so
    // the UI can say "can't reach the server" instead of a generic error.
    throw new ApiError("Could not reach the backend — is it running on " + API_BASE + "?", 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail || detail; } catch { /* body wasn't JSON */ }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ============================================================
// AUTH — token lives in React state only (no localStorage/sessionStorage
// per artifact constraints), so a page refresh signs the user out. That's
// expected for this environment; a real deployment would add a refresh
// flow, not browser storage inside this sandbox.
// ============================================================
const AuthContext = createContext(null);
function useAuth() { return useContext(AuthContext); }

function AuthProvider({ children }) {
  const [auth, setAuth] = useState(null); // { token, role, name, userId }

  const login = async (email, password) => {
    const data = await apiFetch("/auth/login", { method: "POST", body: { email, password } });
    const next = { token: data.access_token, role: data.role, name: data.name, userId: data.user_id };
    setAuth(next);
    return next;
  };
  const logout = () => setAuth(null);

  return (
    <AuthContext.Provider value={{ ...auth, isAuthed: !!auth, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

/** GET a list endpoint; refetches when path/params/token change or reload() is called. */
function useApiList(path, { params, enabled = true } = {}) {
  const { token } = useAuth();
  const [state, setState] = useState({ data: [], loading: true, error: null });
  const [reloadKey, setReloadKey] = useState(0);
  const paramsKey = JSON.stringify(params || {});

  useEffect(() => {
    if (!enabled || !token) return;
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    apiFetch(path, { token, params }).then(
      (data) => { if (!cancelled) setState({ data, loading: false, error: null }); },
      (err) => { if (!cancelled) setState({ data: [], loading: false, error: err.message }); }
    );
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, token, paramsKey, reloadKey, enabled]);

  return { ...state, reload: () => setReloadKey((k) => k + 1) };
}

// ============================================================
// DESIGN TOKENS
// ============================================================
const FONT_IMPORT = `@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');`;

const C = {
  bg: "#F4F6F2", panel: "#FFFFFF", panelDeep: "#ECEEE7",
  sidebar: "#16211C", sidebarSoft: "#22302A",
  ink: "#1B1F1D", inkSoft: "#5B615C", inkFaint: "#9A9C96",
  jade: "#1F6F54", jadeDeep: "#17543F", jadeSoft: "#E4EEE9",
  amber: "#D98E2E", amberSoft: "#FBF0DF",
  rust: "#B54A3C", rustSoft: "#F6E9E6",
  blue: "#3B6EA5", blueSoft: "#E7EEF5",
  gray: "#8B8E88", graySoft: "#EEEEEA",
  hairline: "#DDD9D0", hairlineSoft: "#E8E5DC", white: "#FFFFFF",
};
const F = { d: "'Space Grotesk', sans-serif", b: "'Inter', sans-serif", m: "'IBM Plex Mono', monospace" };

const PALETTE = [C.jade, C.amber, C.blue, C.rust, "#6B5B95", "#3D8A72"];
function initials(name) { return (name || "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase(); }
function colorForId(id) {
  let hash = 0;
  const s = String(id || "");
  for (let i = 0; i < s.length; i++) hash = s.charCodeAt(i) + ((hash << 5) - hash);
  return PALETTE[Math.abs(hash) % PALETTE.length];
}
function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

// ============================================================
// PRIMITIVES
// ============================================================
function StatusPill({ status }) {
  const map = {
    active: [C.jade, C.jadeSoft, "Active"], warning: [C.amber, C.amberSoft, "Warning"],
    error: [C.rust, C.rustSoft, "Error"], inactive: [C.gray, C.graySoft, "Inactive"],
    Running: [C.jade, C.jadeSoft, "Running"], Paused: [C.amber, C.amberSoft, "Paused"],
    Completed: [C.blue, C.blueSoft, "Completed"], Failed: [C.rust, C.rustSoft, "Failed"],
    Scheduled: [C.gray, C.graySoft, "Scheduled"], Draft: [C.gray, C.graySoft, "Draft"],
    Connected: [C.jade, C.jadeSoft, "Connected"],
  };
  const [fg, bg, label] = map[status] || [C.gray, C.graySoft, status];
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-medium" style={{ fontFamily: F.b, color: fg, background: bg, borderRadius: 6 }}>
      <CircleDot size={9} color={fg} />{label}
    </span>
  );
}

function KpiCard({ label, value, accent, sub }) {
  return (
    <div className="flex-1 min-w-[150px] px-4 py-3.5" style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8, borderLeft: `2.5px solid ${accent}` }}>
      <div className="text-[11px] font-medium mb-1.5" style={{ fontFamily: F.b, color: C.inkSoft }}>{label}</div>
      <div className="text-[21px] font-semibold" style={{ fontFamily: F.d, color: C.ink }}>{value}</div>
      {sub && <div className="text-[11px] mt-1" style={{ fontFamily: F.m, color: C.inkFaint }}>{sub}</div>}
    </div>
  );
}

function Table({ columns, children }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8, overflow: "hidden" }}>
      <table className="w-full border-collapse">
        <thead>
          <tr style={{ background: C.panelDeep, borderBottom: `1px solid ${C.hairline}` }}>
            {columns.map((c) => <th key={c} className="text-left px-3.5 py-2.5 text-[11px] font-semibold" style={{ fontFamily: F.b, color: C.inkSoft }}>{c}</th>)}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
function Td({ children, mono }) {
  return <td className="px-3.5 py-2.5 text-[12.5px]" style={{ fontFamily: mono ? F.m : F.b, color: C.ink, borderBottom: `1px solid ${C.hairlineSoft}` }}>{children}</td>;
}
function IconBtn({ icon: Icon, onClick, active }) {
  return (
    <button onClick={onClick} className="p-1.5 shrink-0" style={{ borderRadius: 6, background: active ? C.jadeSoft : "transparent" }}>
      <Icon size={16} color={active ? C.jade : C.inkSoft} />
    </button>
  );
}
function Modal({ title, onClose, children, width = 480 }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(27,31,29,0.45)" }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ width, maxHeight: "85vh", overflowY: "auto", background: C.panel, borderRadius: 10, border: `1px solid ${C.hairline}` }}>
        <div className="flex items-center justify-between px-5 py-3.5" style={{ borderBottom: `1px solid ${C.hairline}` }}>
          <span className="text-[14px] font-semibold" style={{ fontFamily: F.d, color: C.ink }}>{title}</span>
          <button onClick={onClose}><X size={17} color={C.inkSoft} /></button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
function Field({ label, children }) {
  return (
    <div className="mb-3.5">
      <label className="block text-[11.5px] font-medium mb-1.5" style={{ fontFamily: F.b, color: C.inkSoft }}>{label}</label>
      {children}
    </div>
  );
}
const inputStyle = { fontFamily: F.b, fontSize: 13, color: C.ink, width: "100%", padding: "8px 10px", border: `1px solid ${C.hairline}`, borderRadius: 7, outline: "none", background: C.white };
function PrimaryBtn({ children, onClick, full, disabled }) {
  return <button disabled={disabled} onClick={onClick} className={`px-4 py-2 text-[12.5px] font-medium text-white ${full ? "w-full" : ""}`} style={{ fontFamily: F.b, background: disabled ? C.gray : C.jade, borderRadius: 7, opacity: disabled ? 0.7 : 1 }}>{children}</button>;
}
function GhostBtn({ children, onClick }) {
  return <button onClick={onClick} className="px-4 py-2 text-[12.5px] font-medium" style={{ fontFamily: F.b, color: C.inkSoft, border: `1px solid ${C.hairline}`, borderRadius: 7 }}>{children}</button>;
}
function EmptyState({ icon: Icon, title, desc, phase }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
      <div className="w-12 h-12 flex items-center justify-center mb-4" style={{ background: C.panelDeep, borderRadius: 10 }}><Icon size={22} color={C.inkSoft} /></div>
      <div className="text-[15px] font-semibold mb-1.5" style={{ fontFamily: F.d, color: C.ink }}>{title}</div>
      <div className="text-[12.5px] max-w-[380px] leading-relaxed mb-3" style={{ fontFamily: F.b, color: C.inkSoft }}>{desc}</div>
      {phase && <span className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1" style={{ fontFamily: F.b, color: C.amber, background: C.amberSoft, borderRadius: 6 }}><Construction size={11} /> Planned for the next build phase</span>}
    </div>
  );
}
function PageHeader({ title, desc, action }) {
  return (
    <div className="flex items-start justify-between mb-5">
      <div>
        <div className="text-[19px] font-semibold" style={{ fontFamily: F.d, color: C.ink }}>{title}</div>
        {desc && <div className="text-[12.5px] mt-0.5" style={{ fontFamily: F.b, color: C.inkSoft }}>{desc}</div>}
      </div>
      {action}
    </div>
  );
}

/** Loading skeleton for table-shaped content — real fetches take real time. */
function TableSkeleton({ rows = 6 }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8, overflow: "hidden" }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-3 px-3.5 py-3" style={{ borderBottom: i < rows - 1 ? `1px solid ${C.hairlineSoft}` : "none" }}>
          {[30, 20, 15, 10, 10, 15].map((w, j) => (
            <div key={j} style={{ width: `${w}%`, height: 12, background: C.hairlineSoft, borderRadius: 4, animation: "relay-pulse 1.4s ease-in-out infinite" }} />
          ))}
        </div>
      ))}
      <style>{`@keyframes relay-pulse { 0%,100% { opacity: 0.5 } 50% { opacity: 1 } }`}</style>
    </div>
  );
}

/** Real errors get shown, not swallowed — with a retry that re-fires the fetch. */
function ErrorBanner({ message, onRetry }) {
  return (
    <div className="flex items-center justify-between px-4 py-3" style={{ background: C.rustSoft, border: `1px solid ${C.rust}33`, borderRadius: 8 }}>
      <div className="flex items-center gap-2">
        <WifiOff size={15} color={C.rust} />
        <span className="text-[12.5px]" style={{ fontFamily: F.b, color: C.rust }}>{message}</span>
      </div>
      {onRetry && (
        <button onClick={onRetry} className="flex items-center gap-1 text-[11.5px] font-medium px-2.5 py-1" style={{ fontFamily: F.b, color: C.rust, border: `1px solid ${C.rust}`, borderRadius: 6 }}>
          <RefreshCw size={12} /> Retry
        </button>
      )}
    </div>
  );
}

// ============================================================
// LOGIN — real POST /auth/login, controlled inputs (not defaultValue).
// ============================================================
function LoginScreen() {
  const { login } = useAuth();
  const [email, setEmail] = useState("deepa@relaycrm.io");
  const [password, setPassword] = useState("password123");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="w-full h-full flex items-center justify-center" style={{ background: C.sidebar }}>
      <form onSubmit={submit} style={{ width: 380, background: C.panel, borderRadius: 12 }} className="px-7 py-8">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-7 h-7 flex items-center justify-center" style={{ background: C.jade, borderRadius: 7 }}><InboxIcon size={15} color="white" /></div>
          <span className="text-[17px] font-semibold" style={{ fontFamily: F.d, color: C.ink }}>Relay CRM</span>
        </div>
        <p className="text-[12px] mb-6" style={{ fontFamily: F.b, color: C.inkSoft }}>WhatsApp communication & automation platform</p>

        <Field label="Work email">
          <input style={inputStyle} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
        <Field label="Password">
          <input style={inputStyle} type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>

        {error && (
          <div className="flex items-center gap-2 px-3 py-2 mb-3.5" style={{ background: C.rustSoft, borderRadius: 7 }}>
            <AlertCircle size={13} color={C.rust} />
            <span className="text-[11.5px]" style={{ fontFamily: F.b, color: C.rust }}>{error}</span>
          </div>
        )}

        <PrimaryBtn full disabled={submitting}>{submitting ? "Signing in..." : "Sign in"}</PrimaryBtn>
        <p className="text-[11px] text-center mt-4" style={{ fontFamily: F.b, color: C.inkFaint }}>
          Seeded logins — Super Admin: deepa@relaycrm.io · Admin: rahul.sharma@relaycrm.io<br />password for both: password123
        </p>
      </form>
    </div>
  );
}

// ============================================================
// SIDEBAR + TOPBAR
// ============================================================
const NAV_ADMIN = [
  { key: "dashboard", label: "Dashboard", icon: LayoutGrid },
  { key: "inbox", label: "Inbox", icon: InboxIcon },
  { key: "numbers", label: "WhatsApp Numbers", icon: Phone },
  { key: "crm", label: "Contacts / CRM", icon: Contact },
  { key: "campaigns", label: "Campaigns", icon: Megaphone },
  { key: "automations", label: "Automations", icon: Workflow },
  { key: "chatbot", label: "Chatbot", icon: Bot },
  { key: "analytics", label: "Analytics", icon: BarChart3 },
  { key: "settings", label: "Settings", icon: SettingsIcon },
];
const NAV_SUPER = [
  { key: "dashboard", label: "Dashboard", icon: LayoutGrid },
  { key: "admins", label: "Admins", icon: Users },
  { key: "numbers", label: "WhatsApp Numbers", icon: Phone },
  { key: "inbox", label: "Inbox", icon: InboxIcon },
  { key: "crm", label: "Contacts / CRM", icon: Contact },
  { key: "campaigns", label: "Campaigns", icon: Megaphone },
  { key: "automations", label: "Automations", icon: Workflow },
  { key: "chatbot", label: "Chatbot", icon: Bot },
  { key: "analytics", label: "Analytics", icon: BarChart3 },
  { key: "activity", label: "Activity Logs", icon: ScrollText },
  { key: "settings", label: "Settings", icon: SettingsIcon },
];

function Sidebar({ role, page, setPage }) {
  const { logout } = useAuth();
  const items = role === "super_admin" ? NAV_SUPER : NAV_ADMIN;
  return (
    <div className="h-full flex flex-col shrink-0" style={{ width: 216, background: C.sidebar }}>
      <div className="flex items-center gap-2 px-4 pt-5 pb-5">
        <div className="w-7 h-7 flex items-center justify-center" style={{ background: C.jade, borderRadius: 7 }}><InboxIcon size={15} color="white" /></div>
        <span className="text-[15.5px] font-semibold" style={{ fontFamily: F.d, color: "white" }}>Relay CRM</span>
      </div>
      <div className="flex-1 overflow-y-auto px-2.5">
        {items.map((it) => {
          const active = page === it.key;
          const Icon = it.icon;
          return (
            <button key={it.key} onClick={() => setPage(it.key)} className="w-full flex items-center gap-2.5 px-3 py-2 mb-0.5 text-left" style={{ borderRadius: 7, background: active ? C.sidebarSoft : "transparent" }}>
              <Icon size={15} color={active ? "white" : "#8CA398"} />
              <span className="text-[12.5px]" style={{ fontFamily: F.b, fontWeight: active ? 600 : 500, color: active ? "white" : "#9DB2A5" }}>{it.label}</span>
            </button>
          );
        })}
      </div>
      <div className="px-2.5 pb-4 pt-2" style={{ borderTop: `1px solid #2A3A32` }}>
        <button onClick={logout} className="w-full flex items-center gap-2.5 px-3 py-2 text-left">
          <LogOut size={15} color="#8CA398" />
          <span className="text-[12.5px]" style={{ fontFamily: F.b, color: "#9DB2A5" }}>Sign out</span>
        </button>
      </div>
    </div>
  );
}

function Topbar() {
  const { role, name } = useAuth();
  return (
    <div className="flex items-center justify-between px-5 py-3 shrink-0" style={{ background: C.panel, borderBottom: `1px solid ${C.hairline}` }}>
      <div className="flex items-center gap-2 px-3 py-1.5 w-[320px]" style={{ background: C.bg, border: `1px solid ${C.hairline}`, borderRadius: 7 }}>
        <Search size={14} color={C.inkSoft} />
        <input placeholder="Search contacts, numbers, campaigns..." className="bg-transparent outline-none text-[12.5px] w-full placeholder:text-[#9A9C96]" style={{ fontFamily: F.b, color: C.ink }} />
      </div>
      <div className="flex items-center gap-4">
        <Bell size={17} color={C.inkSoft} />
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 flex items-center justify-center text-[11px] font-semibold text-white" style={{ background: C.jade, borderRadius: 7 }}>{initials(name)}</div>
          <div>
            <div className="text-[12px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{name}</div>
            <div className="text-[10.5px]" style={{ fontFamily: F.b, color: C.inkFaint }}>{role === "super_admin" ? "Super Admin" : "Admin"}</div>
          </div>
          <ChevronDown size={13} color={C.inkSoft} />
        </div>
      </div>
    </div>
  );
}

// ============================================================
// DASHBOARDS
// ============================================================
function SuperAdminDashboard() {
  const { token } = useAuth();
  const admins = useApiList("/admins");
  const numbers = useApiList("/numbers");
  const summary = useApiList("/analytics/summary");

  if (admins.error || numbers.error || summary.error) {
    return <ErrorBanner message={admins.error || numbers.error || summary.error} onRetry={() => { admins.reload(); numbers.reload(); summary.reload(); }} />;
  }
  if (admins.loading || numbers.loading || summary.loading) return <TableSkeleton rows={8} />;

  const nums = numbers.data;
  const s = summary.data;
  const activeCount = nums.filter((n) => n.status === "active").length;
  const warnErr = nums.filter((n) => n.status === "warning" || n.status === "error").length;
  const inactive = nums.filter((n) => n.status === "inactive").length;

  return (
    <div>
      <PageHeader title="Control center" desc={`Monitoring ${admins.data.length} admins across ${nums.length} WhatsApp numbers`} />
      <div className="flex flex-wrap gap-3 mb-6">
        <KpiCard label="Total Admins" value={admins.data.length} accent={C.jade} />
        <KpiCard label="WhatsApp Numbers" value={nums.length} accent={C.jade} sub={`${activeCount} active`} />
        <KpiCard label="Warning / Error" value={warnErr} accent={C.amber} />
        <KpiCard label="Inactive" value={inactive} accent={C.gray} />
        <KpiCard label="Messages Sent" value={s.messages_sent.toLocaleString()} accent={C.blue} />
        <KpiCard label="Delivered" value={s.delivered.toLocaleString()} accent={C.blue} />
        <KpiCard label="Failed" value={s.failed.toLocaleString()} accent={C.rust} />
        <KpiCard label="Conversion Rate" value={`${s.conversion_rate}%`} accent={C.jade} />
      </div>

      <div className="text-[13px] font-semibold mb-2.5" style={{ fontFamily: F.d, color: C.ink }}>Admin performance</div>
      <Table columns={["Admin", "Numbers", "Sent", "Replies", "Leads", "Conversion", "Status"]}>
        {admins.data.map((a) => (
          <tr key={a.id}>
            <Td><span className="font-medium">{a.name}</span></Td>
            <Td>{a.numbers_count}</Td>
            <Td mono>{a.messages_sent.toLocaleString()}</Td>
            <Td mono>{a.replies.toLocaleString()}</Td>
            <Td mono>{a.leads}</Td>
            <Td mono>{a.conversion_rate}%</Td>
            <Td><StatusPill status={a.status} /></Td>
          </tr>
        ))}
      </Table>

      <div className="text-[13px] font-semibold mb-2.5 mt-6" style={{ fontFamily: F.d, color: C.ink }}>WhatsApp number monitoring</div>
      <Table columns={["Number", "Status", "Messages Today", "Delivery Rate", "Reply Rate", "Last Activity"]}>
        {nums.slice(0, 8).map((n) => (
          <tr key={n.id}>
            <Td mono>{n.phone}</Td>
            <Td><StatusPill status={n.status} /></Td>
            <Td mono>{n.messages_today.toLocaleString()}</Td>
            <Td mono>{n.delivery_rate}%</Td>
            <Td mono>{n.reply_rate}%</Td>
            <Td>{fmtDateTime(n.last_activity_at)}</Td>
          </tr>
        ))}
      </Table>
      <div className="text-[11.5px] mt-2" style={{ fontFamily: F.b, color: C.inkFaint }}>Showing 8 of {nums.length} numbers — full table on the WhatsApp Numbers page.</div>
    </div>
  );
}

function AdminDashboard() {
  const numbers = useApiList("/numbers");
  const automations = useApiList("/automations");
  const summary = useApiList("/analytics/summary");

  if (numbers.error || automations.error || summary.error) {
    return <ErrorBanner message={numbers.error || automations.error || summary.error} onRetry={() => { numbers.reload(); automations.reload(); summary.reload(); }} />;
  }
  if (numbers.loading || automations.loading || summary.loading) return <TableSkeleton rows={6} />;

  const s = summary.data;
  const runningCount = automations.data.filter((a) => a.status === "Running").length;

  return (
    <div>
      <PageHeader title="Your workspace" desc={`${numbers.data.length} WhatsApp numbers · ${runningCount} active automations`} />
      <div className="flex flex-wrap gap-3 mb-6">
        <KpiCard label="Messages Sent" value={s.messages_sent.toLocaleString()} accent={C.jade} />
        <KpiCard label="Delivered" value={s.delivered.toLocaleString()} accent={C.blue} />
        <KpiCard label="Replies" value={s.replies.toLocaleString()} accent={C.jade} />
        <KpiCard label="Leads" value={s.leads} accent={C.amber} />
        <KpiCard label="Conversion Rate" value={`${s.conversion_rate}%`} accent={C.jade} />
      </div>

      <div className="text-[13px] font-semibold mb-2.5" style={{ fontFamily: F.d, color: C.ink }}>Automation progress</div>
      <Table columns={["Automation", "Status", "Recipients", "Sent", "Delivered", "Replies", "Progress"]}>
        {automations.data.map((a) => {
          const progress = a.recipients ? Math.round((a.sent / a.recipients) * 100) : 0;
          return (
            <tr key={a.id}>
              <Td>{a.name}</Td>
              <Td><StatusPill status={a.status} /></Td>
              <Td mono>{a.recipients.toLocaleString()}</Td>
              <Td mono>{a.sent.toLocaleString()}</Td>
              <Td mono>{a.delivered.toLocaleString()}</Td>
              <Td mono>{a.replies}</Td>
              <Td>
                <div className="flex items-center gap-2">
                  <div style={{ width: 60, height: 5, background: C.hairlineSoft, borderRadius: 3 }}><div style={{ width: `${progress}%`, height: 5, background: C.jade, borderRadius: 3 }} /></div>
                  <span className="text-[11px]" style={{ fontFamily: F.m, color: C.inkSoft }}>{progress}%</span>
                </div>
              </Td>
            </tr>
          );
        })}
      </Table>
    </div>
  );
}

// ============================================================
// WHATSAPP NUMBERS
// ============================================================
function NumbersPage({ role }) {
  const { token } = useAuth();
  const [statusFilter, setStatusFilter] = useState("all");
  const [showAdd, setShowAdd] = useState(false);
  const [pageNum, setPageNum] = useState(1);
  const perPage = 10;

  const numbers = useApiList("/numbers", { params: { status: statusFilter === "all" ? undefined : statusFilter } });
  const admins = useApiList("/admins", { enabled: role === "super_admin" });

  const totalPages = Math.max(1, Math.ceil(numbers.data.length / perPage));
  const paged = numbers.data.slice((pageNum - 1) * perPage, pageNum * perPage);

  return (
    <div>
      <PageHeader title="WhatsApp Numbers" desc={numbers.loading ? "Loading..." : `${numbers.data.length} numbers connected`} action={role === "super_admin" && <PrimaryBtn onClick={() => setShowAdd(true)}><span className="flex items-center gap-1.5"><Plus size={13} /> Add number</span></PrimaryBtn>} />

      <div className="flex gap-1.5 mb-3">
        {["all", "active", "warning", "error", "inactive"].map((s) => (
          <button key={s} onClick={() => { setStatusFilter(s); setPageNum(1); }} className="text-[11.5px] px-3 py-1.5 font-medium capitalize" style={{ fontFamily: F.b, borderRadius: 6, color: statusFilter === s ? "white" : C.inkSoft, background: statusFilter === s ? C.ink : C.panel, border: `1px solid ${statusFilter === s ? C.ink : C.hairline}` }}>{s}</button>
        ))}
      </div>

      {numbers.error && <ErrorBanner message={numbers.error} onRetry={numbers.reload} />}
      {numbers.loading && <TableSkeleton rows={8} />}
      {!numbers.loading && !numbers.error && (
        <>
          <Table columns={["Phone Number", "Display Name", "Status", "Messages Today", "Delivery Rate", "Reply Rate", "Last Activity"]}>
            {paged.map((n) => (
              <tr key={n.id}>
                <Td mono>{n.phone}</Td>
                <Td>{n.display_name}</Td>
                <Td><StatusPill status={n.status} /></Td>
                <Td mono>{n.messages_today.toLocaleString()}</Td>
                <Td mono>{n.delivery_rate}%</Td>
                <Td mono>{n.reply_rate}%</Td>
                <Td>{fmtDateTime(n.last_activity_at)}</Td>
              </tr>
            ))}
          </Table>
          <div className="flex items-center justify-between mt-3">
            <span className="text-[11.5px]" style={{ fontFamily: F.b, color: C.inkSoft }}>Page {pageNum} of {totalPages} · {numbers.data.length} numbers</span>
            <div className="flex gap-1.5">
              <button onClick={() => setPageNum(Math.max(1, pageNum - 1))} className="p-1.5" style={{ border: `1px solid ${C.hairline}`, borderRadius: 6 }}><ChevronLeft size={14} color={C.inkSoft} /></button>
              <button onClick={() => setPageNum(Math.min(totalPages, pageNum + 1))} className="p-1.5" style={{ border: `1px solid ${C.hairline}`, borderRadius: 6 }}><ChevronRight size={14} color={C.inkSoft} /></button>
            </div>
          </div>
        </>
      )}

      {showAdd && (
        <AddNumberModal admins={admins.data} onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); numbers.reload(); }} />
      )}
    </div>
  );
}

function AddNumberModal({ admins, onClose, onAdded }) {
  const { token } = useAuth();
  const [form, setForm] = useState({ phone: "", display_name: "", waba_id: "", phone_number_id: "", admin_id: admins[0]?.id || "" });
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/numbers", { method: "POST", token, body: form });
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="Add WhatsApp number" onClose={onClose}>
      <Field label="Phone number"><input style={inputStyle} placeholder="+91 98XXXXXXXX" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
      <Field label="Display name"><input style={inputStyle} placeholder="Sales Line — Mumbai" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
      <Field label="WhatsApp Business Account ID"><input style={inputStyle} placeholder="WABA ID" value={form.waba_id} onChange={(e) => setForm({ ...form, waba_id: e.target.value })} /></Field>
      <Field label="Phone Number ID"><input style={inputStyle} placeholder="Meta phone number ID" value={form.phone_number_id} onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })} /></Field>
      <Field label="Assigned admin">
        <select style={inputStyle} value={form.admin_id} onChange={(e) => setForm({ ...form, admin_id: e.target.value })}>
          {admins.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      {error && <div className="text-[11.5px] mb-3" style={{ fontFamily: F.b, color: C.rust }}>{error}</div>}
      <div className="flex gap-2 mt-4">
        <GhostBtn onClick={onClose}>Cancel</GhostBtn>
        <PrimaryBtn disabled={submitting || !form.phone || !form.admin_id} onClick={submit}>{submitting ? "Adding..." : "Add number"}</PrimaryBtn>
      </div>
    </Modal>
  );
}

// ============================================================
// ADMINS PAGE (super admin only)
// ============================================================
function AdminsPage() {
  const { token } = useAuth();
  const admins = useApiList("/admins");
  const [showAdd, setShowAdd] = useState(false);

  return (
    <div>
      <PageHeader title="Admins" desc={admins.loading ? "Loading..." : `${admins.data.length} admins`} action={<PrimaryBtn onClick={() => setShowAdd(true)}><span className="flex items-center gap-1.5"><Plus size={13} /> Add admin</span></PrimaryBtn>} />

      {admins.error && <ErrorBanner message={admins.error} onRetry={admins.reload} />}
      {admins.loading && <TableSkeleton rows={8} />}
      {!admins.loading && !admins.error && (
        <Table columns={["Admin", "Email", "Phone", "Numbers", "Messages", "Leads", "Conversion", "Status"]}>
          {admins.data.map((a) => (
            <tr key={a.id}>
              <Td><span className="font-medium">{a.name}</span></Td>
              <Td mono>{a.email}</Td>
              <Td mono>{a.phone}</Td>
              <Td mono>{a.numbers_count}</Td>
              <Td mono>{a.messages_sent.toLocaleString()}</Td>
              <Td mono>{a.leads}</Td>
              <Td mono>{a.conversion_rate}%</Td>
              <Td><StatusPill status={a.status} /></Td>
            </tr>
          ))}
        </Table>
      )}

      {showAdd && <AddAdminModal onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); admins.reload(); }} />}
    </div>
  );
}

function AddAdminModal({ onClose, onAdded }) {
  const { token } = useAuth();
  const [form, setForm] = useState({ name: "", email: "", phone: "", password: "" });
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/admins", { method: "POST", token, body: form });
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="Add admin" onClose={onClose}>
      <Field label="Name"><input style={inputStyle} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Full name" /></Field>
      <Field label="Email"><input style={inputStyle} type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" /></Field>
      <Field label="Phone"><input style={inputStyle} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+91 ..." /></Field>
      <Field label="Temporary password"><input style={inputStyle} type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="At least 8 characters" /></Field>
      {error && <div className="text-[11.5px] mb-3" style={{ fontFamily: F.b, color: C.rust }}>{error}</div>}
      <div className="flex gap-2 mt-4">
        <GhostBtn onClick={onClose}>Cancel</GhostBtn>
        <PrimaryBtn disabled={submitting || !form.name || !form.email || !form.password} onClick={submit}>{submitting ? "Adding..." : "Add admin"}</PrimaryBtn>
      </div>
    </Modal>
  );
}

// ============================================================
// INBOX — real conversations/messages, sends actually POST through the
// mock WhatsApp service on the backend (which logs a fake success).
// ============================================================
const STICKERS = ["🎉", "😊", "👍", "❤️", "🙏", "😂", "🔥", "✅"];

function VoiceComposer({ onCancel, onSend }) {
  const [recording, setRecording] = useState(true);
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [recording]);
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");

  if (recording) {
    return (
      <div className="flex items-center gap-3 px-3 py-2.5" style={{ border: `1px solid ${C.rust}`, borderRadius: 10, background: C.rustSoft }}>
        <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: C.rust }} />
        <span className="text-[12.5px] flex-1" style={{ fontFamily: F.m, color: C.rust }}>Recording... {mm}:{ss}</span>
        <button onClick={onCancel} className="text-[11.5px] font-medium px-2.5 py-1" style={{ fontFamily: F.b, color: C.inkSoft }}>Delete</button>
        <button onClick={() => setRecording(false)} className="flex items-center gap-1 text-[11.5px] font-medium px-2.5 py-1 text-white" style={{ fontFamily: F.b, background: C.rust, borderRadius: 6 }}><Square size={11} /> Stop</button>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-3 px-3 py-2.5" style={{ border: `1px solid ${C.hairline}`, borderRadius: 10 }}>
      <Play size={16} color={C.jade} />
      <div className="flex-1 h-[3px]" style={{ background: C.hairlineSoft, borderRadius: 3 }}><div style={{ width: "40%", height: 3, background: C.jade, borderRadius: 3 }} /></div>
      <span className="text-[11px]" style={{ fontFamily: F.m, color: C.inkSoft }}>{mm}:{ss}</span>
      <button onClick={onCancel}><Trash2 size={14} color={C.inkSoft} /></button>
      <button onClick={() => onSend(seconds)} className="p-1.5" style={{ background: C.jade, borderRadius: 7 }}><Send size={13} color="white" /></button>
    </div>
  );
}

function Bubble({ msg }) {
  if (msg.sender === "system") {
    return <div className="flex justify-center my-3"><span className="text-[11px] px-3 py-1" style={{ fontFamily: F.b, color: C.inkSoft, background: C.panelDeep, borderRadius: 6 }}>{msg.text}</span></div>;
  }
  const isCustomer = msg.sender === "customer";
  const isBot = msg.sender === "bot";
  return (
    <div className={`flex mb-2.5 ${isCustomer ? "justify-start" : "justify-end"}`}>
      <div className="max-w-[62%]">
        {isBot && <div className="flex items-center gap-1 mb-1 justify-end"><Bot size={11} color={C.amber} /><span className="text-[10.5px] font-medium" style={{ fontFamily: F.b, color: C.amber }}>Automated reply</span></div>}
        <div className="px-3.5 py-2.5 text-[13.5px] leading-relaxed" style={{ fontFamily: F.b, color: isCustomer ? C.ink : "white", background: isCustomer ? C.white : isBot ? C.amber : C.jade, border: isCustomer ? `1px solid ${C.hairline}` : "none", borderRadius: isCustomer ? "10px 10px 10px 2px" : "10px 10px 2px 10px" }}>
          {msg.type === "voice" ? (
            <div className="flex items-center gap-2">
              <Play size={13} color={isCustomer ? C.jade : "white"} />
              <div style={{ width: 90, height: 3, background: isCustomer ? C.hairlineSoft : "rgba(255,255,255,0.4)", borderRadius: 3 }}><div style={{ width: "55%", height: 3, background: isCustomer ? C.jade : "white", borderRadius: 3 }} /></div>
              <span className="text-[10.5px]" style={{ fontFamily: F.m }}>{msg.duration_seconds}s</span>
            </div>
          ) : (msg.text || <span style={{ opacity: 0.6 }}>[{msg.type}]</span>)}
        </div>
        <div className={`flex items-center gap-1 mt-1 ${isCustomer ? "justify-start" : "justify-end"}`}>
          <span className="text-[10.5px]" style={{ fontFamily: F.m, color: C.inkFaint }}>{fmtDateTime(msg.created_at)}</span>
          {!isCustomer && <CheckCheck size={13} color={C.jade} />}
        </div>
      </div>
    </div>
  );
}

function CustomerPanel({ contact }) {
  const { token } = useAuth();
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);

  const saveNote = async () => {
    try {
      await apiFetch(`/contacts/${contact.id}/notes`, { method: "POST", token, body: { note } });
    } catch { /* non-critical for the demo — panel closes regardless */ }
    setShowNote(false);
    setNote("");
  };

  return (
    <div className="h-full flex flex-col" style={{ borderLeft: `1px solid ${C.hairline}`, background: C.bg, width: 280 }}>
      <div className="flex flex-col items-center py-4" style={{ borderBottom: `1px solid ${C.hairline}` }}>
        <div className="w-12 h-12 flex items-center justify-center text-[16px] font-semibold text-white mb-2" style={{ background: colorForId(contact.id), borderRadius: 9 }}>{initials(contact.name)}</div>
        <span className="text-[13.5px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{contact.name}</span>
        <span className="text-[11.5px]" style={{ fontFamily: F.m, color: C.inkSoft }}>{contact.phone}</span>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3.5 space-y-2.5">
        <div className="flex items-center justify-between"><span className="text-[11.5px]" style={{ fontFamily: F.b, color: C.inkSoft }}>Email</span><span className="text-[11.5px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{contact.email || "—"}</span></div>
        <div style={{ borderTop: `1px solid ${C.hairlineSoft}` }} className="pt-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11.5px]" style={{ fontFamily: F.b, color: C.inkSoft }}>Notes</span>
            <button onClick={() => setShowNote(true)} className="text-[11px] font-medium" style={{ fontFamily: F.b, color: C.jade }}>+ Add</button>
          </div>
        </div>
      </div>
      {showNote && (
        <Modal title="Add note" onClose={() => setShowNote(false)} width={360}>
          <textarea style={{ ...inputStyle, minHeight: 90 }} placeholder="Write a note about this customer..." value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex gap-2 mt-3"><GhostBtn onClick={() => setShowNote(false)}>Cancel</GhostBtn><PrimaryBtn onClick={saveNote}>Save note</PrimaryBtn></div>
        </Modal>
      )}
    </div>
  );
}

function InboxPage() {
  const { token } = useAuth();
  const [filter, setFilter] = useState("all");
  const conversations = useApiList("/inbox/conversations", { params: { status: filter === "all" ? undefined : filter } });
  const [activeConvId, setActiveConvId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showVoice, setShowVoice] = useState(false);
  const [showStickers, setShowStickers] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  // pick the first conversation once the list loads
  useEffect(() => {
    if (!activeConvId && conversations.data.length > 0) setActiveConvId(conversations.data[0].id);
  }, [conversations.data, activeConvId]);

  useEffect(() => {
    if (!activeConvId || !token) return;
    let cancelled = false;
    setDetailLoading(true);
    apiFetch(`/inbox/conversations/${activeConvId}`, { token }).then(
      (d) => { if (!cancelled) { setDetail(d); setDetailLoading(false); } },
      () => { if (!cancelled) setDetailLoading(false); }
    );
    return () => { cancelled = true; };
  }, [activeConvId, token]);

  const sendMessage = async (payload) => {
    if (!activeConvId) return;
    setSending(true);
    try {
      await apiFetch(`/inbox/conversations/${activeConvId}/messages`, { method: "POST", token, body: payload });
      const fresh = await apiFetch(`/inbox/conversations/${activeConvId}`, { token });
      setDetail(fresh);
      setDraft("");
    } catch (err) {
      // surfaced inline near the composer rather than blowing away the thread
      setDetail((d) => d && { ...d, _sendError: err.message });
    } finally {
      setSending(false);
    }
  };

  const statusColor = { needs_human: C.rust, bot: C.amber, human: C.jade };

  if (conversations.error) return <ErrorBanner message={conversations.error} onRetry={conversations.reload} />;

  return (
    <div className="flex" style={{ height: "calc(100vh - 108px)", background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8, overflow: "hidden" }}>
      <div className="flex flex-col shrink-0" style={{ width: 300, borderRight: `1px solid ${C.hairline}` }}>
        <div className="px-3.5 pt-3.5 pb-2.5">
          <div className="flex gap-1">
            {[["all", "All"], ["needs_human", "Needs human"], ["bot", "Bot"]].map(([k, l]) => (
              <button key={k} onClick={() => setFilter(k)} className="text-[11px] px-2 py-1 font-medium" style={{ fontFamily: F.b, borderRadius: 5, color: filter === k ? "white" : C.inkSoft, background: filter === k ? C.ink : "transparent" }}>{l}</button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {conversations.loading && <div className="px-3.5 py-6 text-[12px]" style={{ fontFamily: F.b, color: C.inkFaint }}>Loading conversations...</div>}
          {!conversations.loading && conversations.data.length === 0 && <div className="px-3.5 py-6 text-[12px]" style={{ fontFamily: F.b, color: C.inkFaint }}>No conversations yet.</div>}
          {conversations.data.map((c) => {
            const active = c.id === activeConvId;
            return (
              <button key={c.id} onClick={() => setActiveConvId(c.id)} className="w-full text-left px-3.5 py-2.5 flex items-start gap-2.5" style={{ borderBottom: `1px solid ${C.hairlineSoft}`, background: active ? C.panelDeep : "transparent", borderLeft: active ? `2px solid ${C.jade}` : "2px solid transparent" }}>
                <div className="w-8 h-8 flex items-center justify-center text-[11px] font-semibold text-white shrink-0" style={{ background: colorForId(c.contact.id), borderRadius: 7 }}>{initials(c.contact.name)}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className="text-[12.5px] font-medium truncate" style={{ fontFamily: F.b, color: C.ink }}>{c.contact.name}</span>
                    <span className="text-[10px] shrink-0" style={{ fontFamily: F.m, color: C.inkFaint }}>{fmtDateTime(c.last_message_at)}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="w-[6px] h-[6px] rounded-full shrink-0" style={{ background: statusColor[c.status] }} />
                    <span className="text-[11.5px] truncate" style={{ fontFamily: F.b, color: C.inkSoft }}>{c.contact.phone}</span>
                  </div>
                </div>
                {c.unread_count > 0 && <span className="text-[10px] font-semibold text-white w-[16px] h-[16px] flex items-center justify-center shrink-0 mt-0.5" style={{ background: C.jade, borderRadius: 4, fontFamily: F.m }}>{c.unread_count}</span>}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        {!detail || detailLoading ? (
          <div className="flex-1 flex items-center justify-center text-[12.5px]" style={{ fontFamily: F.b, color: C.inkFaint }}>{detailLoading ? "Loading conversation..." : "Select a conversation"}</div>
        ) : (
          <>
            <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: `1px solid ${C.hairline}` }}>
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 flex items-center justify-center text-[11px] font-semibold text-white" style={{ background: colorForId(detail.contact.id), borderRadius: 7 }}>{initials(detail.contact.name)}</div>
                <div>
                  <div className="text-[13px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{detail.contact.name}</div>
                  <div className="text-[11px]" style={{ fontFamily: F.m, color: C.inkSoft }}>{detail.contact.phone} · {detail.whatsapp_number_phone}</div>
                </div>
              </div>
              {detail.status === "needs_human" && <span className="text-[10.5px] font-medium px-2 py-1" style={{ fontFamily: F.b, color: C.rust, background: C.rustSoft, borderRadius: 5 }}>Needs human</span>}
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {detail.messages.length === 0 && <div className="text-[12px] text-center py-8" style={{ fontFamily: F.b, color: C.inkFaint }}>No messages yet.</div>}
              {detail.messages.map((m) => <Bubble key={m.id} msg={m} />)}
            </div>
            <div className="px-4 py-3" style={{ borderTop: `1px solid ${C.hairline}` }}>
              {detail._sendError && <div className="text-[11.5px] mb-2" style={{ fontFamily: F.b, color: C.rust }}>{detail._sendError}</div>}
              {showVoice ? (
                <VoiceComposer onCancel={() => setShowVoice(false)} onSend={(secs) => { setShowVoice(false); sendMessage({ type: "voice", duration_seconds: secs, media_url: "mock://voice.ogg" }); }} />
              ) : (
                <div className="relative flex items-end gap-1.5 px-2.5 py-1.5" style={{ border: `1px solid ${C.hairline}`, borderRadius: 10 }}>
                  <IconBtn icon={Paperclip} />
                  <IconBtn icon={ImageIcon} onClick={() => sendMessage({ type: "image", media_url: "mock://image.jpg" })} />
                  <IconBtn icon={Video} onClick={() => sendMessage({ type: "video", media_url: "mock://video.mp4" })} />
                  <IconBtn icon={Smile} onClick={() => setShowStickers(!showStickers)} active={showStickers} />
                  <textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Write a reply..." rows={1}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && draft.trim()) { e.preventDefault(); sendMessage({ type: "text", text: draft }); } }}
                    className="flex-1 bg-transparent outline-none resize-none text-[13px] py-1.5" style={{ fontFamily: F.b, color: C.ink }} />
                  <IconBtn icon={Mic} onClick={() => setShowVoice(true)} />
                  <button disabled={sending || !draft.trim()} onClick={() => sendMessage({ type: "text", text: draft })} className="p-2" style={{ background: sending ? C.gray : C.jade, borderRadius: 7 }}><Send size={14} color="white" /></button>
                  {showStickers && (
                    <div className="absolute bottom-11 left-2 grid grid-cols-4 gap-1 p-2" style={{ background: C.white, border: `1px solid ${C.hairline}`, borderRadius: 8 }}>
                      {STICKERS.map((s) => <button key={s} onClick={() => { setShowStickers(false); sendMessage({ type: "sticker", media_url: `mock://sticker/${s}` }); }} className="text-[20px] p-1.5">{s}</button>)}
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {detail && <CustomerPanel contact={detail.contact} />}
    </div>
  );
}

// ============================================================
// CRM — server-side pagination and filtering (matches /contacts exactly).
// ============================================================
const CONTACT_STATUSES = ["New", "Contacted", "Replied", "Qualified", "Demo", "Converted", "Lost"];

function CrmPage() {
  const { token } = useAuth();
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [pageNum, setPageNum] = useState(1);
  const [selected, setSelected] = useState(null);
  const [result, setResult] = useState({ items: [], total: 0, total_pages: 1 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
    apiFetch("/contacts", { token, params: { page: pageNum, per_page: 10, status: statusFilter === "all" ? undefined : statusFilter, search: search || undefined } })
      .then((d) => { if (!cancelled) { setResult(d); setError(null); } })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [token, pageNum, statusFilter, search]);

  const statusColor = { New: C.blue, Contacted: C.amber, Replied: C.jade, Qualified: C.jade, Demo: C.blue, Converted: C.jade, Lost: C.rust };

  return (
    <div>
      <PageHeader title="Contacts / CRM" desc={loading ? "Loading..." : `${result.total} contacts`} />
      <div className="flex items-center gap-2 mb-3">
        <input placeholder="Search name or phone..." value={search} onChange={(e) => { setSearch(e.target.value); setPageNum(1); }} style={{ ...inputStyle, width: 220 }} />
        <div className="flex gap-1.5 flex-wrap">
          <button onClick={() => { setStatusFilter("all"); setPageNum(1); }} className="text-[11.5px] px-3 py-1.5 font-medium" style={{ fontFamily: F.b, borderRadius: 6, color: statusFilter === "all" ? "white" : C.inkSoft, background: statusFilter === "all" ? C.ink : C.panel, border: `1px solid ${statusFilter === "all" ? C.ink : C.hairline}` }}>All</button>
          {CONTACT_STATUSES.map((s) => (
            <button key={s} onClick={() => { setStatusFilter(s); setPageNum(1); }} className="text-[11.5px] px-3 py-1.5 font-medium" style={{ fontFamily: F.b, borderRadius: 6, color: statusFilter === s ? "white" : C.inkSoft, background: statusFilter === s ? C.ink : C.panel, border: `1px solid ${statusFilter === s ? C.ink : C.hairline}` }}>{s}</button>
          ))}
        </div>
      </div>

      {error && <ErrorBanner message={error} onRetry={() => setPageNum((p) => p)} />}
      {loading && <TableSkeleton rows={8} />}
      {!loading && !error && (
        <>
          <Table columns={["Name", "Phone", "Status", "Tags", "Last Contact", ""]}>
            {result.items.map((c) => (
              <tr key={c.id}>
                <Td><button onClick={() => setSelected(c)} className="font-medium" style={{ color: C.ink }}>{c.name}</button></Td>
                <Td mono>{c.phone}</Td>
                <Td><span className="text-[11px] font-medium px-2 py-0.5" style={{ fontFamily: F.b, color: statusColor[c.status], background: C.panelDeep, borderRadius: 5 }}>{c.status}</span></Td>
                <Td><div className="flex gap-1">{(c.tags || []).slice(0, 2).map((t) => <span key={t} className="text-[10px] px-1.5 py-0.5" style={{ fontFamily: F.b, color: C.jade, background: C.jadeSoft, borderRadius: 4 }}>{t}</span>)}</div></Td>
                <Td>{fmtDate(c.last_contact_at)}</Td>
                <Td><Eye size={13} color={C.inkSoft} /></Td>
              </tr>
            ))}
          </Table>
          <div className="flex items-center justify-between mt-3">
            <span className="text-[11.5px]" style={{ fontFamily: F.b, color: C.inkSoft }}>Page {pageNum} of {result.total_pages} · {result.total} contacts</span>
            <div className="flex gap-1.5">
              <button onClick={() => setPageNum(Math.max(1, pageNum - 1))} className="p-1.5" style={{ border: `1px solid ${C.hairline}`, borderRadius: 6 }}><ChevronLeft size={14} color={C.inkSoft} /></button>
              <button onClick={() => setPageNum(Math.min(result.total_pages, pageNum + 1))} className="p-1.5" style={{ border: `1px solid ${C.hairline}`, borderRadius: 6 }}><ChevronRight size={14} color={C.inkSoft} /></button>
            </div>
          </div>
        </>
      )}

      {selected && (
        <Modal title={selected.name} onClose={() => setSelected(null)} width={440}>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-11 h-11 flex items-center justify-center text-[14px] font-semibold text-white" style={{ background: colorForId(selected.id), borderRadius: 9 }}>{initials(selected.name)}</div>
            <div><div className="text-[13px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{selected.name}</div><div className="text-[11.5px]" style={{ fontFamily: F.m, color: C.inkSoft }}>{selected.phone}</div></div>
          </div>
          {[["Email", selected.email || "—"], ["City", selected.city || "—"], ["Status", selected.status], ["Last Contact", fmtDate(selected.last_contact_at)], ["Created", fmtDate(selected.created_at)]].map(([k, v]) => (
            <div key={k} className="flex items-center justify-between py-1.5" style={{ borderBottom: `1px solid ${C.hairlineSoft}` }}>
              <span className="text-[12px]" style={{ fontFamily: F.b, color: C.inkSoft }}>{k}</span>
              <span className="text-[12px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{v}</span>
            </div>
          ))}
        </Modal>
      )}
    </div>
  );
}

// ============================================================
// CAMPAIGNS — list is real; the wizard really POSTs then launches.
// ============================================================
const CAMPAIGN_STEPS = ["Details", "Number", "Message", "Schedule", "Review", "Launch"];

function CreateCampaignModal({ numbers, onClose, onCreated }) {
  const { token } = useAuth();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({ name: "", whatsapp_number_id: numbers[0]?.id || "", message_type: "text", message_text: "" });
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const last = step === CAMPAIGN_STEPS.length - 1;

  const launch = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const created = await apiFetch("/campaigns", { method: "POST", token, body: { name: form.name, whatsapp_number_id: form.whatsapp_number_id, message_type: form.message_type, message_text: form.message_text } });
      await apiFetch(`/campaigns/${created.id}/launch`, { method: "POST", token });
      onCreated();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="Create campaign" onClose={onClose} width={520}>
      <div className="flex items-center gap-1 mb-5 flex-wrap">
        {CAMPAIGN_STEPS.map((s, i) => (
          <React.Fragment key={s}>
            <div className="flex items-center gap-1.5">
              <div className="w-5 h-5 flex items-center justify-center text-[10px] font-semibold" style={{ fontFamily: F.b, borderRadius: 999, background: i <= step ? C.jade : C.hairlineSoft, color: i <= step ? "white" : C.inkSoft }}>{i + 1}</div>
              <span className="text-[10.5px] font-medium" style={{ fontFamily: F.b, color: i === step ? C.ink : C.inkFaint }}>{s}</span>
            </div>
            {i < CAMPAIGN_STEPS.length - 1 && <div style={{ width: 10, height: 1, background: C.hairline }} />}
          </React.Fragment>
        ))}
      </div>

      {step === 0 && <Field label="Campaign name"><input style={inputStyle} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Diwali Offers Broadcast" /></Field>}
      {step === 1 && (
        <Field label="Send from WhatsApp number">
          <select style={inputStyle} value={form.whatsapp_number_id} onChange={(e) => setForm({ ...form, whatsapp_number_id: e.target.value })}>
            {numbers.map((n) => <option key={n.id} value={n.id}>{n.phone} — {n.display_name}</option>)}
          </select>
        </Field>
      )}
      {step === 2 && <Field label="Message text"><textarea style={{ ...inputStyle, minHeight: 80 }} value={form.message_text} onChange={(e) => setForm({ ...form, message_text: e.target.value })} placeholder="Write your campaign message..." /></Field>}
      {step === 3 && <div className="text-[12px] px-3 py-2" style={{ fontFamily: F.b, color: C.inkSoft, background: C.panelDeep, borderRadius: 6 }}>This build launches immediately on confirm — scheduling UI is here for review, wiring to a delayed send needs a background worker (see backend README).</div>}
      {step === 4 && (
        <div className="space-y-2">
          {[["Name", form.name || "—"], ["Number", numbers.find((n) => n.id === form.whatsapp_number_id)?.phone || "—"], ["Message", form.message_text || "—"]].map(([k, v]) => (
            <div key={k} className="flex items-center justify-between py-1.5" style={{ borderBottom: `1px solid ${C.hairlineSoft}` }}>
              <span className="text-[12px]" style={{ fontFamily: F.b, color: C.inkSoft }}>{k}</span>
              <span className="text-[12px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{v}</span>
            </div>
          ))}
        </div>
      )}
      {last && (
        <div className="flex flex-col items-center py-6 text-center">
          <div className="w-11 h-11 flex items-center justify-center mb-3" style={{ background: C.jadeSoft, borderRadius: 999 }}><Zap size={18} color={C.jade} /></div>
          <div className="text-[13.5px] font-semibold mb-1" style={{ fontFamily: F.d, color: C.ink }}>Ready to launch</div>
          <div className="text-[12px]" style={{ fontFamily: F.b, color: C.inkSoft }}>This creates a real Campaign row and calls the launch endpoint — no messages actually go out while the WhatsApp integration is mocked.</div>
        </div>
      )}
      {error && <div className="text-[11.5px] mt-2" style={{ fontFamily: F.b, color: C.rust }}>{error}</div>}
      <div className="flex justify-between mt-5">
        <GhostBtn onClick={() => (step > 0 ? setStep(step - 1) : onClose())}>{step > 0 ? "Back" : "Cancel"}</GhostBtn>
        <PrimaryBtn disabled={submitting || (step === 0 && !form.name)} onClick={() => (last ? launch() : setStep(step + 1))}>{submitting ? "Launching..." : last ? "Launch campaign" : "Next"}</PrimaryBtn>
      </div>
    </Modal>
  );
}

function CampaignsPage() {
  const campaigns = useApiList("/campaigns");
  const numbers = useApiList("/numbers");
  const [showCreate, setShowCreate] = useState(false);

  return (
    <div>
      <PageHeader title="Campaigns" desc={campaigns.loading ? "Loading..." : `${campaigns.data.length} campaigns`} action={<PrimaryBtn onClick={() => setShowCreate(true)}><span className="flex items-center gap-1.5"><Plus size={13} /> Create campaign</span></PrimaryBtn>} />
      {campaigns.error && <ErrorBanner message={campaigns.error} onRetry={campaigns.reload} />}
      {campaigns.loading && <TableSkeleton rows={6} />}
      {!campaigns.loading && !campaigns.error && (
        <Table columns={["Campaign", "Recipients", "Sent", "Delivered", "Read", "Replies", "Failed", "Status", "Created"]}>
          {campaigns.data.map((c) => (
            <tr key={c.id}>
              <Td><span className="font-medium">{c.name}</span></Td>
              <Td mono>{c.recipients.toLocaleString()}</Td>
              <Td mono>{c.sent.toLocaleString()}</Td>
              <Td mono>{c.delivered.toLocaleString()}</Td>
              <Td mono>{c.read.toLocaleString()}</Td>
              <Td mono>{c.replies}</Td>
              <Td mono>{c.failed}</Td>
              <Td><StatusPill status={c.status} /></Td>
              <Td mono>{fmtDate(c.created_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
      {showCreate && !numbers.loading && (
        <CreateCampaignModal numbers={numbers.data} onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); campaigns.reload(); }} />
      )}
    </div>
  );
}

// ============================================================
// AUTOMATIONS — list + status are real; the visual canvas below is
// illustrative (the backend stores `flow` as JSON but there's no field-level
// edit endpoint yet — activating/pausing is real, editing nodes isn't).
// ============================================================
function FlowNode({ label, sub, color, icon: Icon }) {
  return (
    <div className="px-3.5 py-2.5 flex items-center gap-2.5" style={{ background: C.white, border: `1px solid ${C.hairline}`, borderLeft: `3px solid ${color}`, borderRadius: 8, minWidth: 200 }}>
      <div className="w-6 h-6 flex items-center justify-center shrink-0" style={{ background: `${color}1A`, borderRadius: 6 }}><Icon size={12} color={color} /></div>
      <div><div className="text-[12px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{label}</div>{sub && <div className="text-[10.5px]" style={{ fontFamily: F.b, color: C.inkSoft }}>{sub}</div>}</div>
    </div>
  );
}
function FlowArrow() { return <div className="flex justify-center py-1"><ArrowDown size={14} color={C.inkFaint} /></div>; }

function AutomationsPage() {
  const { token } = useAuth();
  const automations = useApiList("/automations");
  const [openBuilder, setOpenBuilder] = useState(null);
  const [busy, setBusy] = useState(false);

  const toggleStatus = async (automation) => {
    const next = automation.status === "Running" ? "Paused" : "Running";
    setBusy(true);
    try {
      await apiFetch(`/automations/${automation.id}`, { method: "PATCH", token, body: { status: next } });
      automations.reload();
      if (openBuilder?.id === automation.id) setOpenBuilder({ ...automation, status: next });
    } finally {
      setBusy(false);
    }
  };

  if (openBuilder) {
    return (
      <div>
        <button onClick={() => setOpenBuilder(null)} className="flex items-center gap-1 text-[12px] font-medium mb-3" style={{ fontFamily: F.b, color: C.inkSoft }}><ChevronLeft size={14} /> Back to automations</button>
        <PageHeader title={openBuilder.name} desc="Visual trigger → action flow (illustrative — activate/pause below is real)" action={
          <PrimaryBtn disabled={busy} onClick={() => toggleStatus(openBuilder)}><span className="flex items-center gap-1.5"><Zap size={13} /> {openBuilder.status === "Running" ? "Pause" : "Activate"}</span></PrimaryBtn>
        } />
        <div style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8 }} className="flex flex-col items-center py-6">
          <FlowNode label="New customer" sub="Trigger" color={C.blue} icon={Zap} />
          <FlowArrow />
          <FlowNode label="Send welcome message" sub="Text + image" color={C.jade} icon={Send} />
          <FlowArrow />
          <FlowNode label="Wait 2 hours" sub="Delay" color={C.gray} icon={Clock} />
          <FlowArrow />
          <FlowNode label="Customer replied?" sub="Condition" color={C.amber} icon={GitBranch} />
          <div className="flex gap-10 mt-1">
            <div className="flex flex-col items-center"><span className="text-[10px] font-semibold mb-1" style={{ fontFamily: F.b, color: C.jade }}>YES</span><div style={{ width: 1, height: 14, background: C.hairline }} /><FlowNode label="Transfer to human" color={C.jade} icon={UserCog} /></div>
            <div className="flex flex-col items-center"><span className="text-[10px] font-semibold mb-1" style={{ fontFamily: F.b, color: C.rust }}>NO</span><div style={{ width: 1, height: 14, background: C.hairline }} /><FlowNode label="Send follow-up" color={C.rust} icon={Send} /></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Automations" desc={automations.loading ? "Loading..." : `${automations.data.length} automations`} />
      {automations.error && <ErrorBanner message={automations.error} onRetry={automations.reload} />}
      {automations.loading && <TableSkeleton rows={5} />}
      {!automations.loading && !automations.error && (
        <Table columns={["Automation", "Status", "Recipients", "Sent", "Delivered", "Replies", "Progress", "Actions"]}>
          {automations.data.map((a) => {
            const progress = a.recipients ? Math.round((a.sent / a.recipients) * 100) : 0;
            return (
              <tr key={a.id}>
                <Td><button onClick={() => setOpenBuilder(a)} className="font-medium" style={{ color: C.ink }}>{a.name}</button></Td>
                <Td><StatusPill status={a.status} /></Td>
                <Td mono>{a.recipients.toLocaleString()}</Td>
                <Td mono>{a.sent.toLocaleString()}</Td>
                <Td mono>{a.delivered.toLocaleString()}</Td>
                <Td mono>{a.replies}</Td>
                <Td><div className="flex items-center gap-2"><div style={{ width: 50, height: 5, background: C.hairlineSoft, borderRadius: 3 }}><div style={{ width: `${progress}%`, height: 5, background: C.jade, borderRadius: 3 }} /></div><span className="text-[10.5px]" style={{ fontFamily: F.m, color: C.inkSoft }}>{progress}%</span></div></Td>
                <Td><button onClick={() => setOpenBuilder(a)} className="flex items-center gap-1 text-[11.5px] font-medium" style={{ fontFamily: F.b, color: C.jade }}>Open <ChevronRight size={12} /></button></Td>
              </tr>
            );
          })}
        </Table>
      )}
    </div>
  );
}

// ============================================================
// CHATBOT — same pattern: list/status real, flow canvas illustrative.
// ============================================================
function ChatbotPage() {
  const chatbots = useApiList("/chatbots");
  const [openBot, setOpenBot] = useState(null);

  if (openBot) {
    return (
      <div>
        <button onClick={() => setOpenBot(null)} className="flex items-center gap-1 text-[12px] font-medium mb-3" style={{ fontFamily: F.b, color: C.inkSoft }}><ChevronLeft size={14} /> Back to chatbots</button>
        <PageHeader title={openBot.name} desc={`Connected to ${chatbots.data.find((b) => b.id === openBot.id)?.whatsapp_number_id ? "a WhatsApp number" : ""}`} />
        <div style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8 }} className="flex flex-col items-center py-6">
          <FlowNode label="START" color={C.blue} icon={Zap} />
          <FlowArrow />
          <FlowNode label="Welcome message" color={C.jade} icon={Send} />
          <FlowArrow />
          <FlowNode label="Main menu" sub="4 options" color={C.amber} icon={ListTree} />
          <div style={{ width: 1, height: 14, background: C.hairline }} className="mt-1" />
          <div className="flex gap-6 flex-wrap justify-center mt-1">
            {(openBot.flow?.menu || ["Product Information", "Pricing", "FAQ", "Talk to Human"]).map((label) => (
              <FlowNode key={label} label={label} sub={label === "Talk to Human" ? "Assign agent" : "Automated answer"} color={label === "Talk to Human" ? C.rust : C.jade} icon={label === "Talk to Human" ? UserCog : MessageCircle} />
            ))}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {(openBot.keywords || []).map((k) => <span key={k} className="text-[10.5px] px-2 py-1" style={{ fontFamily: F.b, color: C.amber, background: C.amberSoft, borderRadius: 5 }}>{k}</span>)}
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Chatbot" desc={chatbots.loading ? "Loading..." : `${chatbots.data.length} bots`} />
      {chatbots.error && <ErrorBanner message={chatbots.error} onRetry={chatbots.reload} />}
      {chatbots.loading && <TableSkeleton rows={4} />}
      {!chatbots.loading && !chatbots.error && (
        <Table columns={["Bot Name", "Status", "Conversations", "Automated Responses", "Human Handoffs", "Actions"]}>
          {chatbots.data.map((b) => (
            <tr key={b.id}>
              <Td><button onClick={() => setOpenBot(b)} className="font-medium" style={{ color: C.ink }}>{b.name}</button></Td>
              <Td><StatusPill status={b.status} /></Td>
              <Td mono>{b.conversations.toLocaleString()}</Td>
              <Td mono>{b.automated_responses.toLocaleString()}</Td>
              <Td mono>{b.human_handoffs}</Td>
              <Td><button onClick={() => setOpenBot(b)} className="flex items-center gap-1 text-[11.5px] font-medium" style={{ fontFamily: F.b, color: C.jade }}>Open builder <ChevronRight size={12} /></button></Td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}

// ============================================================
// ANALYTICS — KPIs, funnel, and campaign-performance chart are real
// (backed by /analytics/summary, /analytics/funnel, /campaigns). The
// messages-over-time and delivery/read/reply-by-day charts need a per-day
// events table the backend doesn't have yet (only rolling today-counters
// exist) — those two stay clearly-labeled synthetic data until that exists.
// ============================================================
function ChartCard({ title, children, height = 220, note }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8 }} className="p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[12px] font-semibold" style={{ fontFamily: F.b, color: C.ink }}>{title}</div>
        {note && <span className="text-[10px] px-1.5 py-0.5" style={{ fontFamily: F.b, color: C.amber, background: C.amberSoft, borderRadius: 4 }}>{note}</span>}
      </div>
      <div style={{ height }}><ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer></div>
    </div>
  );
}
const axisStyle = { fontSize: 10.5, fontFamily: F.m, fill: C.inkSoft };

function synthTimeSeries(seed) {
  return Array.from({ length: 14 }, (_, i) => {
    const day = i + 1;
    const sent = seed + Math.round(Math.sin(i / 2) * (seed * 0.1)) + i * (seed * 0.01);
    return { day: `Day ${day}`, sent: Math.round(sent), delivered: Math.round(sent * 0.94), read: Math.round(sent * 0.61), replies: Math.round(sent * 0.14) };
  });
}

function AnalyticsPage() {
  const summary = useApiList("/analytics/summary");
  const funnel = useApiList("/analytics/funnel");
  const campaigns = useApiList("/campaigns");

  if (summary.error) return <ErrorBanner message={summary.error} onRetry={() => { summary.reload(); funnel.reload(); campaigns.reload(); }} />;
  if (summary.loading || funnel.loading || campaigns.loading) return <TableSkeleton rows={6} />;

  const s = summary.data;
  const series = synthTimeSeries(Math.max(s.messages_sent / 14, 200));
  const funnelData = funnel.data.map((f, i) => ({ ...f, fill: ["#1F6F54", "#2E8567", "#4A9C7E", "#D98E2E", "#B54A3C"][i] }));

  return (
    <div>
      <PageHeader title="Analytics" desc="Live totals from every WhatsApp number and campaign" />
      <div className="flex flex-wrap gap-3 mb-4">
        <KpiCard label="Messages Sent" value={s.messages_sent.toLocaleString()} accent={C.jade} />
        <KpiCard label="Delivered" value={s.delivered.toLocaleString()} accent={C.blue} />
        <KpiCard label="Read" value={s.read.toLocaleString()} accent={C.blue} />
        <KpiCard label="Replies" value={s.replies.toLocaleString()} accent={C.amber} />
        <KpiCard label="Leads" value={s.leads} accent={C.jade} />
        <KpiCard label="Conversion Rate" value={`${s.conversion_rate}%`} accent={C.jade} />
      </div>

      <div className="grid grid-cols-2 gap-4 mb-4">
        <ChartCard title="Messages over time" note="Illustrative">
          <LineChart data={series}>
            <CartesianGrid stroke={C.hairlineSoft} vertical={false} />
            <XAxis dataKey="day" tick={axisStyle} interval={2} axisLine={{ stroke: C.hairline }} tickLine={false} />
            <YAxis tick={axisStyle} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ fontFamily: F.b, fontSize: 12, borderRadius: 8, border: `1px solid ${C.hairline}` }} />
            <Line type="monotone" dataKey="sent" stroke={C.jade} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="replies" stroke={C.amber} strokeWidth={2} dot={false} />
          </LineChart>
        </ChartCard>
        <ChartCard title="Delivery vs read vs reply" note="Illustrative">
          <BarChart data={series.slice(-7)}>
            <CartesianGrid stroke={C.hairlineSoft} vertical={false} />
            <XAxis dataKey="day" tick={axisStyle} axisLine={{ stroke: C.hairline }} tickLine={false} />
            <YAxis tick={axisStyle} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ fontFamily: F.b, fontSize: 12, borderRadius: 8, border: `1px solid ${C.hairline}` }} />
            <Bar dataKey="delivered" fill={C.jade} radius={[3, 3, 0, 0]} />
            <Bar dataKey="read" fill={C.blue} radius={[3, 3, 0, 0]} />
            <Bar dataKey="replies" fill={C.amber} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartCard>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <ChartCard title="Conversion funnel" height={240}>
          <FunnelChart>
            <Tooltip contentStyle={{ fontFamily: F.b, fontSize: 12, borderRadius: 8, border: `1px solid ${C.hairline}` }} />
            <Funnel dataKey="value" data={funnelData} isAnimationActive={false}>
              <LabelList position="right" dataKey="name" fill={C.inkSoft} stroke="none" fontSize={10.5} fontFamily={F.b} />
            </Funnel>
          </FunnelChart>
        </ChartCard>
        <ChartCard title="Campaign performance" height={240}>
          <BarChart layout="vertical" data={campaigns.data.slice(0, 5).map((c) => ({ name: c.name.length > 16 ? c.name.slice(0, 16) + "…" : c.name, delivered: c.delivered }))} margin={{ left: 10 }}>
            <CartesianGrid stroke={C.hairlineSoft} horizontal={false} />
            <XAxis type="number" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="name" tick={{ ...axisStyle, fontFamily: F.b }} width={110} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ fontFamily: F.b, fontSize: 12, borderRadius: 8, border: `1px solid ${C.hairline}` }} />
            <Bar dataKey="delivered" fill={C.jade} radius={[0, 3, 3, 0]} />
          </BarChart>
        </ChartCard>
      </div>
    </div>
  );
}

// ============================================================
// API KEYS — super admin only, values always masked.
// ============================================================
function ApiKeysPage() {
  const { token } = useAuth();
  const keys = useApiList("/api-keys");
  const [showAdd, setShowAdd] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const test = async (id) => {
    setBusyId(id);
    try { await apiFetch(`/api-keys/${id}/test`, { method: "POST", token }); keys.reload(); } finally { setBusyId(null); }
  };
  const remove = async (id) => {
    setBusyId(id);
    try { await apiFetch(`/api-keys/${id}`, { method: "DELETE", token }); keys.reload(); } finally { setBusyId(null); }
  };

  return (
    <div>
      <PageHeader title="API Keys" desc="Manage WhatsApp API provider connections" action={<PrimaryBtn onClick={() => setShowAdd(true)}><span className="flex items-center gap-1.5"><Plus size={13} /> Add API key</span></PrimaryBtn>} />
      {keys.error && <ErrorBanner message={keys.error} onRetry={keys.reload} />}
      {keys.loading && <TableSkeleton rows={2} />}
      {!keys.loading && !keys.error && (
        <div className="space-y-2.5">
          {keys.data.map((k) => (
            <div key={k.id} className="flex items-center justify-between px-4 py-3" style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8 }}>
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 flex items-center justify-center" style={{ background: C.jadeSoft, borderRadius: 7 }}><Key size={14} color={C.jade} /></div>
                <div><div className="text-[13px] font-medium" style={{ fontFamily: F.b, color: C.ink }}>{k.provider}</div><div className="text-[11.5px]" style={{ fontFamily: F.m, color: C.inkSoft }}>{k.masked}</div></div>
              </div>
              <div className="flex items-center gap-3">
                <StatusPill status={k.status} />
                <button disabled={busyId === k.id} onClick={() => test(k.id)} className="text-[11.5px] font-medium" style={{ fontFamily: F.b, color: C.jade }}>Test</button>
                <button disabled={busyId === k.id} onClick={() => remove(k.id)} className="text-[11.5px] font-medium" style={{ fontFamily: F.b, color: C.rust }}>Remove</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {showAdd && <AddApiKeyModal onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); keys.reload(); }} />}
    </div>
  );
}

function AddApiKeyModal({ onClose, onAdded }) {
  const { token } = useAuth();
  const [form, setForm] = useState({ provider: "Meta", api_key: "", business_account_id: "", phone_number_id: "" });
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/api-keys", { method: "POST", token, body: form });
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="Add API key" onClose={onClose}>
      <Field label="Provider">
        <select style={inputStyle} value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>
          <option>Meta</option><option>360dialog</option><option>Twilio</option><option>Gupshup</option>
        </select>
      </Field>
      <Field label="API Key"><input style={inputStyle} type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="Paste API key" /></Field>
      <Field label="Business Account ID"><input style={inputStyle} value={form.business_account_id} onChange={(e) => setForm({ ...form, business_account_id: e.target.value })} placeholder="WABA ID" /></Field>
      <Field label="Phone Number ID"><input style={inputStyle} value={form.phone_number_id} onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })} placeholder="Phone number ID" /></Field>
      {error && <div className="text-[11.5px] mb-3" style={{ fontFamily: F.b, color: C.rust }}>{error}</div>}
      <div className="flex gap-2 mt-4"><GhostBtn onClick={onClose}>Cancel</GhostBtn><PrimaryBtn disabled={submitting || !form.api_key} onClick={submit}>{submitting ? "Saving..." : "Save & connect"}</PrimaryBtn></div>
    </Modal>
  );
}

function ActivityLogsPage() {
  const logs = useApiList("/activity-logs", { params: { limit: 50 } });
  return (
    <div>
      <PageHeader title="Activity Logs" desc="System-wide activity across all admins and numbers" />
      {logs.error && <ErrorBanner message={logs.error} onRetry={logs.reload} />}
      {logs.loading && <TableSkeleton rows={6} />}
      {!logs.loading && !logs.error && (
        logs.data.length === 0 ? (
          <div style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8 }}>
            <EmptyState icon={ScrollText} title="No activity yet" desc="Actions like adding a number, launching a campaign, or starting an automation will show up here." />
          </div>
        ) : (
          <Table columns={["Time", "Type", "Event"]}>
            {logs.data.map((a) => (
              <tr key={a.id}><Td mono>{fmtDateTime(a.created_at)}</Td><Td>{a.type}</Td><Td>{a.message}</Td></tr>
            ))}
          </Table>
        )
      )}
    </div>
  );
}

function SettingsPage({ role }) {
  const { token, name } = useAuth();
  const [tab, setTab] = useState("profile");
  const tabs = role === "super_admin"
    ? [["profile", "Profile"], ["apikeys", "API Keys"]]
    : [["profile", "Profile"]];

  return (
    <div>
      <PageHeader title="Settings" />
      <div className="flex gap-1.5 mb-4">
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className="text-[12px] px-3 py-1.5 font-medium" style={{ fontFamily: F.b, borderRadius: 6, color: tab === k ? "white" : C.inkSoft, background: tab === k ? C.ink : C.panel, border: `1px solid ${tab === k ? C.ink : C.hairline}` }}>{l}</button>
        ))}
      </div>
      {tab === "apikeys" ? <ApiKeysPage /> : (
        <div style={{ background: C.panel, border: `1px solid ${C.hairline}`, borderRadius: 8, maxWidth: 420 }} className="px-5 py-4">
          <Field label="Full name"><input style={inputStyle} defaultValue={name} disabled /></Field>
          <div className="text-[11.5px]" style={{ fontFamily: F.b, color: C.inkFaint }}>Profile editing isn't wired to an update endpoint yet — this reads your real logged-in name.</div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// ROOT APP
// ============================================================
function Shell() {
  const { isAuthed, role } = useAuth();
  const [page, setPage] = useState("dashboard");

  if (!isAuthed) return <div style={{ height: 640 }}><LoginScreen /></div>;

  const pageMap = {
    dashboard: role === "super_admin" ? <SuperAdminDashboard /> : <AdminDashboard />,
    admins: <AdminsPage />,
    numbers: <NumbersPage role={role} />,
    inbox: <InboxPage />,
    crm: <CrmPage />,
    campaigns: <CampaignsPage />,
    automations: <AutomationsPage />,
    chatbot: <ChatbotPage />,
    analytics: <AnalyticsPage />,
    activity: <ActivityLogsPage />,
    settings: <SettingsPage role={role} />,
  };

  return (
    <div className="flex" style={{ height: "700px", overflow: "hidden" }}>
      <Sidebar role={role} page={page} setPage={setPage} />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <div className="flex-1 overflow-y-auto px-5 py-5">{pageMap[page]}</div>
      </div>
    </div>
  );
}

export default function RelayCrmApp() {
  return (
    <div style={{ fontFamily: F.b, background: C.bg }}>
      <style>{FONT_IMPORT}</style>
      <AuthProvider>
        <Shell />
      </AuthProvider>
    </div>
  );
}

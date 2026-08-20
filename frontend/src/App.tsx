import { NavLink, Route, Routes } from "react-router-dom";
import AgentsPage from "./pages/AgentsPage";
import ChatPage from "./pages/ChatPage";
import EvalPage from "./pages/EvalPage";
import KeysPage from "./pages/KeysPage";
import McpPage from "./pages/McpPage";
import RunDetailPage from "./pages/RunDetailPage";

const NAV = [
  { to: "/", label: "会话" },
  { to: "/agents", label: "Agents" },
  { to: "/mcp", label: "MCP" },
  { to: "/keys", label: "Keys" },
  { to: "/eval", label: "评估" },
];

export default function App() {
  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="flex items-center gap-6 border-b border-slate-200 bg-white px-6 py-3">
        <span className="font-semibold">MCP Agent Platform</span>
        <nav className="flex gap-4 text-sm">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                isActive
                  ? "font-medium text-blue-600"
                  : "text-slate-500 hover:text-slate-800"
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/mcp" element={<McpPage />} />
          <Route path="/keys" element={<KeysPage />} />
          <Route path="/eval" element={<EvalPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}

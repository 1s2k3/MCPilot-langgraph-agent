import { NavLink, Route, Routes } from "react-router-dom";

const NAV = [
  { to: "/", label: "会话" },
  { to: "/agents", label: "Agents" },
  { to: "/mcp", label: "MCP" },
  { to: "/keys", label: "Keys" },
  { to: "/eval", label: "评估" },
];

function Placeholder({ title }: { title: string }) {
  return (
    <div className="flex h-full items-center justify-center text-slate-400">
      {title} — 开发中（按里程碑交付）
    </div>
  );
}

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
          <Route path="/" element={<Placeholder title="会话" />} />
          <Route path="/agents" element={<Placeholder title="Agents" />} />
          <Route path="/mcp" element={<Placeholder title="MCP" />} />
          <Route path="/keys" element={<Placeholder title="Keys" />} />
          <Route path="/eval" element={<Placeholder title="评估" />} />
          <Route path="/runs/:id" element={<Placeholder title="运行详情" />} />
        </Routes>
      </main>
    </div>
  );
}

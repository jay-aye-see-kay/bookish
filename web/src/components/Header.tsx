import { NavLink } from "react-router";
import { useHealth } from "../api/hooks";
import { useUser } from "../state/user";

function HealthPill() {
  const { data, isLoading, isError } = useHealth();
  let text = "…";
  let color = "bg-gray-100 text-gray-500";
  if (!isLoading) {
    if (isError || !data) {
      text = "backend down";
      color = "bg-red-100 text-red-700";
    } else {
      const embedOk = data.embed_server === "ok";
      text = `${data.corpus.toLocaleString()} books · embed ${embedOk ? "✓" : "down"}`;
      color = embedOk
        ? "bg-green-100 text-green-700"
        : "bg-amber-100 text-amber-700";
    }
  }
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-medium ${color}`}>
      {text}
    </span>
  );
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${
    isActive ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"
  }`;

export function Header() {
  const { username, setUsername } = useUser();

  return (
    <header className="flex flex-wrap items-center gap-4 border-b border-gray-200 px-6 py-3">
      <span className="text-lg font-semibold">Bookish</span>
      <nav className="flex gap-1">
        <NavLink to="/" end className={linkClass}>
          Preferences
        </NavLink>
        <NavLink to="/recommendations" className={linkClass}>
          Recommendations
        </NavLink>
      </nav>
      <div className="ml-auto flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-gray-500">
          user
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-28 rounded-md border border-gray-300 px-2 py-1 text-gray-900 outline-none focus:border-gray-500"
          />
        </label>
        <HealthPill />
      </div>
    </header>
  );
}

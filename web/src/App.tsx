import { Outlet } from "react-router";
import { Header } from "./components/Header";

export function App() {
  return (
    <div className="min-h-screen bg-white text-gray-900">
      <Header />
      <main>
        <Outlet />
      </main>
    </div>
  );
}

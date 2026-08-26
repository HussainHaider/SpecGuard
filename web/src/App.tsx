import { NavLink, Route, Routes } from "react-router-dom";

import Ops from "./routes/Ops";
import Review from "./routes/Review";

export default function App() {
  return (
    <div className="shell">
      <header className="masthead">
        <div className="masthead__brand">
          <span className="masthead__mark" aria-hidden="true" />
          <span className="masthead__name">SpecGuard</span>
          <span className="masthead__tagline">EU food label compliance review</span>
        </div>
        <nav className="masthead__nav">
          <NavLink to="/" end>
            Review
          </NavLink>
          <NavLink to="/ops">Ops</NavLink>
        </nav>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<Review />} />
          <Route path="/ops" element={<Ops />} />
        </Routes>
      </main>

      <footer className="colophon">
        Independent portfolio project. Not affiliated with any retailer. Public regulation
        text, synthetic product data.
      </footer>
    </div>
  );
}

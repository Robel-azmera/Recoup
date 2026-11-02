import { Route, Routes } from 'react-router-dom';

import { Layout } from './components/Layout';
import { useAuth } from './hooks/useAuth';
import { Login } from './pages/Login';
import { Overview } from './pages/Overview';
import { Placeholder } from './pages/Placeholder';
import { Settings } from './pages/Settings';

export default function App() {
  const { token, login, logout } = useAuth();
  if (!token) return <Login onLogin={login} />;

  return (
    <Layout onLogout={logout}>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/disputes" element={<Placeholder title="Disputes" phase="Phase 3" />} />
        <Route
          path="/disputes/:id"
          element={<Placeholder title="Dispute workspace" phase="Phase 3" />}
        />
        <Route
          path="/reconciliation"
          element={<Placeholder title="Reconciliation" phase="Phase 4" />}
        />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Placeholder title="Not found" phase="never" />} />
      </Routes>
    </Layout>
  );
}

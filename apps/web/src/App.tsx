import { Route, Routes } from 'react-router-dom';

import { Layout } from './components/Layout';
import { useAuth } from './hooks/useAuth';
import { useLiveInvalidation } from './hooks/useLive';
import { DisputeWorkspace } from './pages/DisputeWorkspace';
import { Disputes } from './pages/Disputes';
import { Login } from './pages/Login';
import { Overview } from './pages/Overview';
import { Placeholder } from './pages/Placeholder';
import { Reconciliation } from './pages/Reconciliation';
import { Settings } from './pages/Settings';

export default function App() {
  const { token, login, logout } = useAuth();
  if (!token) return <Login onLogin={login} />;
  return <AuthedApp onLogout={logout} />;
}

function AuthedApp({ onLogout }: { onLogout: () => void }) {
  useLiveInvalidation();
  return (
    <Layout onLogout={onLogout}>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/disputes" element={<Disputes />} />
        <Route path="/disputes/:id" element={<DisputeWorkspace />} />
        <Route path="/reconciliation" element={<Reconciliation />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Placeholder title="Not found" phase="never" />} />
      </Routes>
    </Layout>
  );
}

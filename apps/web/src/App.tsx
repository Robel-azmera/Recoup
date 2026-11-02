import { Route, Routes } from 'react-router-dom';

import { Layout } from './components/Layout';
import { Overview } from './pages/Overview';
import { Placeholder } from './pages/Placeholder';

export default function App() {
  return (
    <Layout>
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
        <Route path="/settings" element={<Placeholder title="Settings" phase="Phase 1" />} />
        <Route path="*" element={<Placeholder title="Not found" phase="never" />} />
      </Routes>
    </Layout>
  );
}

import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Library from './components/Library'
import Reader from './components/Reader'
import Review from './components/Review'
import ReviewCenter from './components/ReviewCenter'
import Upload from './components/Upload'
import Jobs from './components/Jobs'
import JobDetail from './components/JobDetail'
import PhaseAWorkspace from './components/PhaseAWorkspace'

function App() {
  return (
    <div className="min-h-screen bg-slate-50">
      <Routes>
        <Route path="/review-center" element={<ReviewCenter />} />
        <Route
          path="/review"
          element={
            <PhaseAWorkspace panel="review">
              <Review />
            </PhaseAWorkspace>
          }
        />
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/upload" replace />} />
          <Route path="library" element={<Library />} />
          <Route path="book/:id" element={<Reader />} />
          <Route path="reader/:id" element={<Navigate to="/book/:id" replace />} />
          <Route path="upload" element={<Upload />} />
          <Route path="jobs" element={<Jobs />} />
          <Route
            path="jobs/:id"
            element={
              <PhaseAWorkspace panel="workbench">
                <JobDetail />
              </PhaseAWorkspace>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </div>
  )
}

export default App

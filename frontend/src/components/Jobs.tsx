// Jobs.tsx is no longer rendered as a standalone page; the workspace
// book list is embedded inside Upload.tsx. The /jobs route is kept
// as a redirect to /upload for backward compatibility.
import { Navigate } from 'react-router-dom'

const Jobs = () => <Navigate to="/upload" replace />

export default Jobs

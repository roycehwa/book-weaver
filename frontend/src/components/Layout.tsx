import { Outlet, Link, useLocation } from 'react-router-dom'

const Layout = () => {
  const location = useLocation()

  const navItems = [
    { path: '/upload', label: '书籍处理' },
    { path: '/review-center', label: '审阅' },
  ]

  const embeddedWorkspace = /^\/jobs\/[^/]+/.test(location.pathname)

  return (
    <div className="min-h-screen flex flex-col">
      {!embeddedWorkspace && <header className="border-b border-[#3a2a22] bg-[#241c16] text-[#f6efe4]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <Link to="/upload" className="flex items-center space-x-3">
              <div className="flex h-9 w-9 items-center justify-center border border-[#e7c7b2]/40 bg-primary-600 font-serif text-lg text-[#fbf7f0]">
                织
              </div>
              <span className="bw-serif text-xl text-[#fbf7f0]">BookWeaver</span>
              <span className="rounded-full border border-[#6f6258] px-2 py-0.5 text-[10px] tracking-[0.16em] text-[#e7dccb]">
                Phase A
              </span>
            </Link>
            <nav className="flex space-x-2">
              {navItems.map((item) => (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`rounded-full px-3 py-1.5 text-sm transition-colors ${
                    location.pathname === item.path
                      ? 'bg-[#fbf7f0] font-medium text-[#241c16]'
                      : 'text-[#e7dccb] hover:bg-[#3b2a22] hover:text-white'
                  }`}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </div>
      </header>}

      <main className={embeddedWorkspace ? 'flex-1' : 'flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8'}>
        <Outlet />
      </main>
    </div>
  )
}

export default Layout

# BookMate PDF Reader - Testing Guide

## 🚀 Quick Start

### Install Dependencies
```bash
cd bookmate/frontend
npm install
```

### Run Tests
```bash
# Run tests in watch mode
npm test

# Run tests once
npm run test:run

# Run with UI
npm run test:ui

# Run with coverage
npm run test:coverage
```

---

## 📁 Test Structure

```
frontend/
├── src/
│   ├── components/
│   │   └── pdf-viewer/
│   │       ├── PdfViewer.tsx              # Component under test
│   │       ├── usePdfLoader.ts            # Hook under test
│   │       ├── types.ts                   # Type definitions
│   │       ├── PdfViewer.css              # Styles
│   │       └── __tests__/
│   │           ├── usePdfLoader.test.ts   # Hook tests (15 cases)
│   │           ├── PdfViewer.test.tsx     # Component tests (42 cases)
│   │           └── integration.test.tsx   # Integration examples
│   └── test/
│       └── setup.ts                       # Test environment setup
├── vite.config.ts                         # Vitest configuration
└── TEST_REPORT.md                         # Full test report
```

---

## 🎯 Test Categories

### 1. Hook Tests (usePdfLoader)

| Category | Tests |
|----------|-------|
| Initial State | Loading state, empty URL |
| PDF Loading | Success, error, exceptions |
| URL Changes | Reload, cleanup |
| Lifecycle | Unmount, race conditions |

### 2. Component Tests (PdfViewer)

| Category | Tests |
|----------|-------|
| Rendering | Loading, error, success states |
| Page Navigation | Next/prev, jump, validation |
| Zoom Controls | In/out/reset, min/max bounds |
| Footer | Progress display |
| Accessibility | ARIA labels |
| Edge Cases | Single page, invalid input |

---

## 🔧 Mock Setup

### Mocking react-pdf
```typescript
vi.mock('react-pdf', () => ({
  Document: ({ children }) => <div>{children}</div>,
  Page: ({ pageNumber }) => <div>Page {pageNumber}</div>,
  pdfjs: { getDocument: vi.fn() },
}));
```

### Mocking usePdfLoader
```typescript
vi.mock('../usePdfLoader', () => ({
  usePdfLoader: () => ({
    pdf: { numPages: 10 },
    loading: false,
    error: null,
    loaded: true,
    numPages: 10,
  }),
}));
```

---

## 🧪 Writing New Tests

### Test a New Feature
```typescript
it('should [expected behavior]', async () => {
  // Arrange
  render(<PdfViewer {...props} />);
  
  // Act
  await userEvent.click(screen.getByLabelText('Next'));
  
  // Assert
  expect(screen.getByText('Page 2')).toBeInTheDocument();
});
```

### Test Async Operations
```typescript
it('should handle async loading', async () => {
  render(<PdfViewer url="test.pdf" />);
  
  // Wait for async operation
  await waitFor(() => {
    expect(screen.getByTestId('pdf-page')).toBeInTheDocument();
  });
});
```

---

## 📊 Coverage Report

Generate coverage report:
```bash
npm run test:coverage
```

View HTML report:
```bash
open coverage/index.html
```

---

## 🐛 Debugging Tests

### Run Single Test
```bash
npm test -- PdfViewer
```

### Run with Logging
```typescript
it('debug test', () => {
  render(<PdfViewer {...props} />);
  screen.debug(); // Logs DOM to console
});
```

### Mock Console
```typescript
const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
// ... test code
consoleSpy.mockRestore();
```

---

## ✅ Checklist for New Features

When adding new features to PdfViewer:

- [ ] Write test for happy path
- [ ] Write test for error cases
- [ ] Write test for edge cases (empty, null, undefined)
- [ ] Update integration test examples if needed
- [ ] Run full test suite: `npm run test:run`
- [ ] Check coverage: `npm run test:coverage`
- [ ] Update TEST_REPORT.md with new test cases

---

## 🔗 Useful Resources

- [Vitest Docs](https://vitest.dev/)
- [React Testing Library](https://testing-library.com/docs/react-testing-library/intro/)
- [Jest DOM Matchers](https://github.com/testing-library/jest-dom)
- [react-pdf](https://github.com/wojtekmaj/react-pdf)

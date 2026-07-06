import { useState, useEffect, useCallback } from 'react';
import { pdfjs } from 'react-pdf';
// 用 vite 的 ?url 让 worker 资源走 dev middleware / 产物 hash 路径，
// 不依赖全局配置或 pdfjs-dist 自带的相对路径（react-pdf 默认值在 vite 下无法解析）。
// eslint-disable-next-line import/no-unresolved
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.js?url';
import type { PdfLoadState } from './types';

// 从react-pdf的pdfjs获取getDocument
const { getDocument } = pdfjs;

// 覆盖 react-pdf 模块内硬编码的相对路径 workerSrc
pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

// 定义PDF文档类型
type PDFDocumentProxy = any;

/**
 * PDF加载Hook
 * 处理PDF文档的异步加载、状态管理和错误处理
 * 
 * @param url - PDF文件的URL地址
 * @returns 包含PDF对象、加载状态、错误信息和页数
 * 
 * @example
 * ```tsx
 * const { pdf, loading, error, numPages } = usePdfLoader('https://example.com/book.pdf');
 * 
 * if (loading) return <Spinner />;
 * if (error) return <Error message={error.message} />;
 * return <Document file={pdf} />;
 * ```
 */
export function usePdfLoader(url: string) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [state, setState] = useState<PdfLoadState>({
    loading: true,
    error: null,
    loaded: false,
  });
  const [numPages, setNumPages] = useState<number>(0);

  /**
   * 重置加载状态
   */
  const resetState = useCallback(() => {
    setState({
      loading: true,
      error: null,
      loaded: false,
    });
    setNumPages(0);
  }, []);

  useEffect(() => {
    // URL为空时直接返回
    if (!url) {
      setState({
        loading: false,
        error: new Error('PDF URL is required'),
        loaded: false,
      });
      return;
    }

    let isCancelled = false;
    let loadedDocument: PDFDocumentProxy | null = null;
    
    resetState();

    const loadPdf = async () => {
      try {
        // 加载PDF文档
        const loadingTask = getDocument({
          url,
          // 启用CORS (跨域支持)
          withCredentials: false,
        });

        const pdfDocument = await loadingTask.promise;
        loadedDocument = pdfDocument;

        // 检查组件是否已卸载或URL是否已更改
        if (isCancelled) {
          pdfDocument.destroy();
          return;
        }

        setPdf(pdfDocument);
        setNumPages(pdfDocument.numPages);
        setState({
          loading: false,
          error: null,
          loaded: true,
        });
      } catch (err) {
        if (isCancelled) return;

        const error = err instanceof Error ? err : new Error('Failed to load PDF');
        console.error('PDF加载失败:', error);
        
        setState({
          loading: false,
          error,
          loaded: false,
        });
        setPdf(null);
        setNumPages(0);
      }
    };

    loadPdf();

    // 清理函数
    return () => {
      isCancelled = true;
      if (loadedDocument) {
        loadedDocument.destroy();
      }
    };
  }, [url, resetState]);

  return {
    pdf,
    loading: state.loading,
    error: state.error,
    loaded: state.loaded,
    numPages,
  };
}

/**
 * 预加载PDF (用于预缓存)
 * 
 * @param url - PDF文件URL
 * @returns Promise<PDFDocumentProxy>
 */
export async function preloadPdf(url: string): Promise<PDFDocumentProxy> {
  const loadingTask = getDocument({ url });
  return loadingTask.promise;
}

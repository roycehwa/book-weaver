/**
 * PDF Viewer Types
 * BookMate - PDF阅读器类型定义
 */

/**
 * PDF文档元数据
 */
export interface PdfDocument {
  /** 文档唯一标识 */
  id?: string;
  /** PDF文件URL */
  url: string;
  /** 文档标题 */
  title?: string;
  /** 文档作者 */
  author?: string;
  /** 总页数 */
  numPages: number;
  /** 文档主题/关键词 */
  subject?: string;
  /** 创建日期 */
  creationDate?: string;
  /** 修改日期 */
  modificationDate?: string;
}

/**
 * PDF页面信息
 */
export interface PdfPage {
  /** 页码 (从1开始) */
  pageNumber: number;
  /** 页面原始宽度 (点) */
  originalWidth: number;
  /** 页面原始高度 (点) */
  originalHeight: number;
  /** 旋转角度 */
  rotation?: number;
  /** 页面缩放比例 */
  scale?: number;
}

/**
 * 章节信息
 */
export interface Chapter {
  /** 章节唯一标识 */
  id: string;
  /** 章节标题 */
  title: string;
  /** 起始页码 */
  startPage: number;
  /** 结束页码 (可选) */
  endPage?: number;
  /** 章节层级 (1=一级标题, 2=二级标题等) */
  level: number;
  /** 父章节ID (用于嵌套章节) */
  parentId?: string;
  /** 子章节列表 */
  children?: Chapter[];
}

/**
 * PDF加载状态
 */
export interface PdfLoadState {
  /** 是否加载中 */
  loading: boolean;
  /** 错误信息 */
  error: Error | null;
  /** 是否加载完成 */
  loaded: boolean;
}

/**
 * PDF渲染选项
 */
export interface PdfRenderOptions {
  /** 缩放比例 (默认1.0) */
  scale: number;
  /** 旋转角度 (0, 90, 180, 270) */
  rotation?: number;
  /** 是否启用文字选择 */
  enableTextSelection?: boolean;
  /** 是否启用注解层 */
  enableAnnotations?: boolean;
}

/**
 * 查看器配置
 */
export interface ViewerConfig {
  /** 默认缩放比例 */
  defaultScale: number;
  /** 最小缩放比例 */
  minScale: number;
  /** 最大缩放比例 */
  maxScale: number;
  /** 缩放步进值 */
  scaleStep: number;
  /** 是否显示页码导航 */
  showPageNavigation: boolean;
  /** 是否显示缩放控制 */
  showZoomControls: boolean;
  /** 是否显示页码输入 */
  showPageInput: boolean;
}
